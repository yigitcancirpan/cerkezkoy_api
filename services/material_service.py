"""Material totals preserve line totals and leave unallocated scrap explicit."""
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import text

TZ = ZoneInfo('Europe/Istanbul')


def material_list(db):
    return [dict(r._mapping) for r in db.execute(text('''
        SELECT material_id, material_code, material_name, model_id, is_active
        FROM materials ORDER BY material_code
    ''')).fetchall()]


def unique_models(materials):
    groups = defaultdict(list)
    for m in materials:
        if m['model_id'] and m['model_id'] > 0:
            groups[m['model_id']].append(m)
    return {k: v[0] for k, v in groups.items() if len(v) == 1}


def split_production(summary, models):
    """Never attribute a mixed shift to its last model. Missing detail is unknown."""
    total = int(summary['total_produced'] or 0)
    detail = summary.get('ideal_cycle_detail') or []
    if not isinstance(detail, list):
        detail = []
    parts = defaultdict(int)
    for row in detail:
        qty = max(0, int(row.get('produced') or 0))
        m = models.get(row.get('model_id'))
        parts[m['material_id'] if m else None] += qty
    if sum(parts.values()) > total:
        return {None: total}
    parts[None] += total - sum(parts.values())
    return {k: v for k, v in parts.items() if v}


def report(db, line_id, start, end, material_id=None, shift=None):
    if end < start or (end-start).days > 92:
        raise HTTPException(422, 'Malzeme raporu aralığı en fazla 93 gün olmalı.')
    materials = material_list(db)
    models = unique_models(materials)
    meta = {m['material_id']: m for m in materials}
    if material_id is not None and material_id != 0 and material_id not in meta:
        raise HTTPException(404, 'Malzeme bulunamadı')
    params = {'lid': line_id, 'start': start, 'end': end, 'shift': shift}
    summaries = [dict(r._mapping) for r in db.execute(text('''
        SELECT shift_date, shift, total_produced, total_scrap, ideal_cycle_detail
        FROM shift_summary WHERE line_id=:lid AND shift_date BETWEEN :start AND :end
        AND (CAST(:shift AS text) IS NULL OR shift=:shift)
    '''), params).fetchall()]
    production = {}
    for s in summaries:
        production[(s['shift_date'], s['shift'])] = split_production(s, models)
    # Fill only shifts without a stored summary; never add logs to an existing summary.
    logs = db.execute(text('''
        SELECT log_id, logged_at, shift, model_id, produced FROM production_log
        WHERE line_id=:lid AND logged_at >= :begin AND logged_at < :finish
        AND (CAST(:shift AS text) IS NULL OR shift=:shift)
        ORDER BY logged_at, log_id
    '''), {**params, 'begin': datetime.combine(start, datetime.min.time(), TZ),
           'finish': datetime.combine(end+timedelta(days=2), datetime.min.time(), TZ)}).fetchall()
    import shift_utils
    shifts = {}
    previous = {}
    live = defaultdict(lambda: defaultdict(int))
    for raw in logs:
        r = dict(raw._mapping)
        at = r['logged_at'].astimezone(TZ)
        ck = (at.date(), r['shift'])
        if ck not in shifts:
            shifts[ck] = shift_utils.shift_by_code(r['shift'], line_id=line_id, dt=at)
        cfg = shifts[ck]
        # Unknown shift codes cannot be placed on a production date reliably.
        if not cfg or cfg.get('code') != r['shift']:
            continue
        day = shift_utils.shift_date_for_datetime(cfg, at)
        key = (day, r['shift'])
        value = max(0, int(r['produced'] or 0))
        old = previous.get(key)
        delta = 0 if old is None else value-old if value >= old else value
        previous[key] = value
        if start <= day <= end and key not in production:
            m = models.get(r['model_id'])
            live[key][m['material_id'] if m else None] += delta
    production.update(live)
    scrap = defaultdict(lambda: defaultdict(int))
    for r in db.execute(text('''
        SELECT production_date, shift, material_id, SUM(qty) AS qty
        FROM scrap_entries WHERE line_id=:lid AND production_date BETWEEN :start AND :end
        AND (CAST(:shift AS text) IS NULL OR shift=:shift)
        GROUP BY production_date, shift, material_id
    '''), params).fetchall():
        scrap[(r.production_date, r.shift)][r.material_id] += int(r.qty)
    # Older summaries can contain scrap with no corresponding entry rows.
    # Preserve that difference as unallocated, never as good production.
    for s in summaries:
        key = (s['shift_date'], s['shift'])
        missing = int(s['total_scrap'] or 0) - sum(scrap[key].values())
        if missing > 0:
            scrap[key][None] += missing
    items = []
    for key in sorted(set(production) | set(scrap)):
        made, defects = production.get(key, {}), scrap[key]
        unknown = defects.get(None, 0)
        for mid in sorted(set(made) | set(defects), key=lambda x: x or 0):
            if material_id is not None and (mid or 0) != material_id:
                continue
            m = meta.get(mid, {})
            qty, bad = made.get(mid, 0), defects.get(mid, 0)
            good = max(0, qty-bad) if key in production and not unknown and bad <= qty else None
            items.append({'date': str(key[0]), 'shift': key[1], 'material_id': mid,
                          'material_code': m.get('material_code', 'BELİRSİZ'),
                          'material_name': m.get('material_name', 'Malzeme eşlemesi yok'),
                          'produced': qty, 'scrap': bad, 'good': good,
                          'unallocated_shift_scrap': unknown,
                          'source': 'özet' if key not in live else 'sayaç kayıtları'})
    return {'items': items, 'materials': materials,
            'total_produced': sum(x['produced'] for x in items),
            'total_scrap': sum(x['scrap'] for x in items),
            'total_good': sum(x['good'] for x in items) if all(x['good'] is not None for x in items) else None,
            'basis': 'Hat OEE’si ortaktır. Malzeme adedi vardiya model detayından; eksik özetler sayaç farklarından gelir. Belirsiz fire varsa sağlam adet — gösterilir. Sayaç örnekleme aralığında model geçişleri kesin ayrıştırılamayabilir.'}


def require_scrap_material(db, line_id, day, shift, material_id, qty):
    # Serializes add/delete for one shift, including shifts without a summary yet.
    db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),
               {'key': f'material-scrap:{line_id}:{day}:{shift}'})
    data = report(db, line_id, day, day, shift=shift)
    candidates = [r for r in data['items'] if r['material_id'] is not None and r['produced'] > 0]
    if material_id is None:
        ids = {r['material_id'] for r in candidates}
        unknown = any(r['material_id'] is None and r['produced'] > 0 for r in data['items'])
        if len(ids) != 1 or unknown:
            raise HTTPException(422, 'Bu vardiyada malzeme seçimi gerekli; belirsiz üretimi bir malzemeye atamayın.')
        material_id = next(iter(ids))
    matched = [r for r in candidates if r['material_id'] == material_id]
    if not matched or sum(r['scrap'] for r in matched)+qty > sum(r['produced'] for r in matched):
        raise HTTPException(422, 'Seçilen malzemenin üretimi bulunamadı veya fire üretimi aşıyor.')
    if sum(r['scrap'] for r in data['items'])+qty > data['total_produced']:
        raise HTTPException(422, 'Toplam fire vardiya üretimini aşıyor.')
    return material_id
