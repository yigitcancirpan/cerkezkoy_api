"""Draft/review/approve API. Writes only planning tables; no external AI calls."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from typing import Literal, Optional
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from models.database import get_db
from services.auto_planning_service import capacity_history, schedule

router = APIRouter(prefix='/auto', tags=['Otomatik Üretim Planı'])


def today():
    return datetime.now(ZoneInfo('Europe/Istanbul')).date()


class Option(BaseModel):
    code: Literal['day8', 'day10', 'day12', 'double']
    capacity: int = Field(ge=0, le=10000000)
    break_extra_capacity: int = Field(default=0, ge=0, le=1000000)
    break_minutes: int = Field(default=0, ge=0, le=240)


class FixedDay(BaseModel):
    date: date
    code: Literal['off', 'day8', 'day10', 'day12', 'double']
    qty: int = Field(ge=0, le=10000000)
    break_work: bool = False


class AutoRequest(BaseModel):
    minimum_closing_stock: Optional[int] = Field(default=None, ge=0, le=10000000)
    start_date: date
    days: int = Field(default=28, ge=1, le=56)
    options: list[Option] = Field(min_length=1, max_length=4)
    workdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    locks: list[FixedDay] = Field(default_factory=list, max_length=56)
    include_planned: bool = False
    dispatch_timing: Literal['before', 'after'] = 'after'
    planning_mode: Literal['balanced_weekly', 'daily_flexible'] = 'balanced_weekly'
    capacity_note: str = Field(default='Elle girilen günlük sağlam adet kapasitesi', max_length=300)


class ApproveRequest(BaseModel):
    acknowledge_warnings: bool = False


PRESETS = {
    'day8': ('08.00–16.00', 8), 'day10': ('08.00–18.00', 10),
    'day12': ('08.00–20.00', 12), 'double': ('08.00–16.00 + 16.00–24.00', 16),
}


def scope(line_id, product_code):
    from routers.planning import _require_scope
    _require_scope(line_id, product_code)


def plain(value):
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def digest(value):
    return sha256(json.dumps(plain(value), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _resolved_break_rows(rows, shift_code, shift_date, line_id):
    """Resolve break scope with the same priority as shift_utils.get_breaks."""
    pg_dow = (shift_date.weekday()+1) % 7
    candidates = []
    for row in rows:
        if row['shift_code'] != shift_code:
            continue
        if row['line_id'] is not None and row['line_id'] != line_id:
            continue
        if row['day_of_week'] is not None and row['day_of_week'] != pg_dow:
            continue
        priority = (2 if row['day_of_week'] is not None else 0) + (1 if row['line_id'] is not None else 0)
        candidates.append((priority, row))
    resolved = {}
    for _priority, row in sorted(candidates, key=lambda item: item[0]):
        resolved[row['break_code']] = row
    return list(resolved.values())


def _break_history(db, summaries, model_id, line_id):
    """Attach estimated good pieces produced inside configured break windows."""
    break_rows = [dict(row._mapping) for row in db.execute(text('''
        SELECT shift_code, break_code, start_minute, end_minute, line_id, day_of_week
        FROM break_schedules WHERE is_active=TRUE AND (line_id IS NULL OR line_id=:lid)
        ORDER BY shift_code, start_minute
    '''), {'lid': line_id}).fetchall()]
    enriched = []
    for source in summaries:
        row = dict(source._mapping) if hasattr(source, '_mapping') else dict(source)
        seconds = int(row.get('planned_base_sec') or 0)
        if seconds <= 0:
            enriched.append(row)
            continue
        shift_date = row['shift_date']
        start_at = datetime.combine(shift_date, time(8), tzinfo=ZoneInfo('Europe/Istanbul'))
        end_at = start_at + timedelta(seconds=seconds)
        windows = []
        for item in _resolved_break_rows(break_rows, row['shift'], shift_date, line_id):
            begin = datetime.combine(shift_date, time(), tzinfo=start_at.tzinfo) + timedelta(minutes=item['start_minute'])
            finish = datetime.combine(shift_date, time(), tzinfo=start_at.tzinfo) + timedelta(minutes=item['end_minute'])
            begin, finish = max(begin, start_at), min(finish, end_at)
            if finish > begin:
                windows.append((begin, finish))
        logs = db.execute(text('''
            SELECT logged_at, model_id, produced FROM production_log
            WHERE line_id=:lid AND shift=:shift AND logged_at>=:start_at AND logged_at<:end_at
            ORDER BY logged_at, log_id
        '''), {'lid': line_id, 'shift': row['shift'], 'start_at': start_at, 'end_at': end_at}).fetchall()
        previous = None
        break_produced = 0
        for log_row in logs:
            log = dict(log_row._mapping) if hasattr(log_row, '_mapping') else dict(log_row)
            produced = int(log.get('produced') or 0)
            delta = 0 if previous is None else produced-previous if produced >= previous else produced
            previous = produced
            if log.get('model_id') == model_id and any(a <= log['logged_at'] < b for a, b in windows):
                break_produced += max(delta, 0)
        produced = int(row.get('total_produced') or 0)
        good = int(row.get('total_good') or 0)
        row['break_good'] = round(break_produced * good / produced) if produced > 0 else 0
        row['break_minutes'] = round(sum((b-a).total_seconds() for a, b in windows)/60)
        enriched.append(row)
    return enriched


def snapshot(db, body, line_id=2, product_code='2962070700'):
    from routers.planning import _config_row
    c = dict(_config_row(db, product_code)._mapping)
    if body.start_date < today():
        raise HTTPException(422, 'Geçmiş günlere otomatik plan uygulanamaz.')
    if body.start_date != c['finished_stock_as_of']:
        raise HTTPException(422, 'Otomatik plan başlangıcı mamul stok tarihiyle aynı olmalı. Başlangıç günü üretim öncesi stok sayımını girin.')
    if Decimal(c['raw_per_piece'] or 0) > 0 and c['raw_stock_as_of'] != body.start_date:
        raise HTTPException(422, 'Hammadde ve mamul stok sayımları aynı başlangıç gününe ait olmalı.')
    if len(set(body.workdays)) != len(body.workdays) or any(x < 0 or x > 6 for x in body.workdays):
        raise HTTPException(422, 'Çalışma günleri tekil ve 0–6 aralığında olmalı.')
    if len(set(x.code for x in body.options)) != len(body.options):
        raise HTTPException(422, 'Çalışma seçenekleri tekrarlanamaz.')
    end = body.start_date + timedelta(days=body.days-1)
    p = {'start': body.start_date, 'end': end, 'lid': line_id, 'code': product_code}
    def rows(sql):
        return [dict(r._mapping) for r in db.execute(text(sql), p).fetchall()]
    orders = rows('''SELECT order_date, order_qty, updated_at FROM planning_orders
        WHERE line_id=:lid AND product_code=:code AND order_date BETWEEN :start AND :end ORDER BY order_date''')
    raw = rows('''SELECT raw_order_id, expected_date, order_qty, status, updated_at FROM planning_raw_orders
        WHERE line_id=:lid AND product_code=:code AND expected_date BETWEEN :start AND :end ORDER BY raw_order_id''')
    daily = rows('''SELECT * FROM planning_daily_capacity
        WHERE line_id=:lid AND product_code=:code AND plan_date BETWEEN :start AND :end ORDER BY plan_date''')
    weekly = rows('''SELECT * FROM planning_weekly_capacity WHERE line_id=:lid AND product_code=:code
        AND week_start BETWEEN CAST(:start AS date)-6 AND :end ORDER BY week_start''')
    receipts = {}
    for r in raw:
        if r['status'] == 'confirmed' or (body.include_planned and r['status'] == 'planned'):
            key = str(r['expected_date'])
            receipts[key] = str(Decimal(receipts.get(key, '0')) + r['order_qty'])
    inputs = {k: c[k] for k in ['finished_stock', 'finished_safety_stock', 'raw_stock_qty',
                               'raw_per_piece', 'raw_scrap_pct', 'raw_safety_stock']}
    if body.minimum_closing_stock is not None:
        inputs['finished_safety_stock'] = body.minimum_closing_stock
    inputs.update(start_date=str(body.start_date), days=body.days, workdays=body.workdays,
                  include_planned=body.include_planned, dispatch_timing=body.dispatch_timing,
                  receipts=receipts, orders={str(r['order_date']): int(r['order_qty']) for r in orders})
    return plain(inputs), digest({'config': c, 'orders': orders, 'raw': raw, 'daily': daily, 'weekly': weekly})


@router.get('/history')
def history(lookback: int = Query(84, ge=7, le=365), line_id: int = Query(2),
            product_code: str = Query('2962070700'), db: Session = Depends(get_db)):
    scope(line_id, product_code)
    from routers.planning import _product_meta
    model_id = _product_meta(db, product_code)['material']['model_id']
    shared = db.execute(text('''SELECT COUNT(*) FROM materials WHERE model_id=:mid AND is_active=TRUE'''),
                        {'mid': model_id}).scalar()
    if model_id is None or shared != 1:
        return {'sample_count': 0, 'gross_good_per_hour': None, 'cautious_good_per_hour': None,
                'warning': 'Ürün/model eşlemesi tekil değil; kapasiteyi elle girin.'}
    rows = db.execute(text('''SELECT shift_date, shift, model_id, total_good, total_produced,
             planned_base_sec, ideal_cycle_detail FROM shift_summary
        WHERE line_id=:lid AND model_id=:mid AND shift_date >= :start AND shift_date < :end
        ORDER BY shift_date, shift'''), {'lid': line_id, 'mid': model_id,
                                       'start': today()-timedelta(days=lookback), 'end': today()}).fetchall()
    result = capacity_history(_break_history(db, rows, model_id, line_id), model_id)
    result['warning'] = ('Bazı vardiya türlerinde 5 örnekten az veri var; kapasiteyi kontrol edin.'
                         if any(x['sample_count'] < 5 for x in result['profiles'].values())
                         else 'Vardiya türleri ve dönüşümlü mola katkıları ayrı hesaplandı.')
    return result


@router.post('/generate', status_code=201)
def generate(body: AutoRequest, line_id: int = Query(2), product_code: str = Query('2962070700'),
             db: Session = Depends(get_db)):
    scope(line_id, product_code)
    db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
    inputs, fingerprint = snapshot(db, body, line_id, product_code)
    options = [{'code': x.code, 'capacity': x.capacity,
                'break_extra_capacity': x.break_extra_capacity,
                'break_minutes': x.break_minutes,
                'label': PRESETS[x.code][0], 'hours': PRESETS[x.code][1]}
               for x in body.options]
    try:
        result = schedule(
            inputs,
            options,
            plain([x.model_dump() for x in body.locks]),
            mode=body.planning_mode,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    result['capacity_note'] = body.capacity_note
    result['options'] = result.pop('option_variants')
    result['warnings'].append('Kapasiteler tahmindir; ekip, mola, bakım ve kalıp uygunluğu plan onayında kontrol edilmelidir.')
    request = plain(body.model_dump())
    run_id = str(uuid4())
    db.execute(text('''INSERT INTO planning_auto_runs (run_id, line_id, product_code, start_date,
        end_date, request, result, input_fingerprint) VALUES
        (:id, :lid, :code, :start, :end, CAST(:request AS jsonb), CAST(:result AS jsonb), :fp)'''),
        {'id': run_id, 'lid': line_id, 'code': product_code, 'start': body.start_date, 'end': body.start_date+timedelta(days=body.days-1),
         'request': json.dumps(request), 'result': json.dumps(result), 'fp': fingerprint})
    db.commit()
    return {'run_id': run_id, 'status': 'draft', 'request': request, 'result': result}


@router.get('/latest')
def latest(start_date: date, days: int = Query(28, ge=1, le=56), line_id: int = Query(2),
           product_code: str = Query('2962070700'), db: Session = Depends(get_db)):
    scope(line_id, product_code)
    row = db.execute(text('''SELECT run_id, status, request, result, created_at, approved_at
        FROM planning_auto_runs WHERE line_id=:lid AND product_code=:code
        AND start_date=:start AND end_date=:end ORDER BY created_at DESC LIMIT 1'''),
        {'lid': line_id, 'code': product_code, 'start': start_date,
         'end': start_date+timedelta(days=days-1)}).fetchone()
    return dict(row._mapping) if row else None


@router.post('/{run_id}/approve')
def approve(run_id: UUID, body: ApproveRequest, line_id: int = Query(2),
            product_code: str = Query('2962070700'), db: Session = Depends(get_db)):
    scope(line_id, product_code)
    db.execute(text("SET LOCAL lock_timeout = '5s'"))
    # Lock the saved draft first (idempotent double-click), then its input tables.
    row = db.execute(text('''SELECT status, request, result, input_fingerprint FROM planning_auto_runs
        WHERE run_id=:id AND line_id=:lid AND product_code=:code FOR UPDATE'''),
        {'id': str(run_id), 'lid': line_id, 'code': product_code}).fetchone()
    if not row:
        raise HTTPException(404, 'Plan bulunamadı.')
    if row.status == 'approved':
        return {'status': 'approved', 'already_approved': True}
    if row.result.get('policy_version') != 'materials_stock_v1':
        raise HTTPException(409, 'Bu taslak eski planlama kuralıyla oluşturulmuş. Vardiya ve mola kapasitesiyle yeniden hesaplayın.')
    request = AutoRequest(**row.request)
    if row.result['warnings'] and not body.acknowledge_warnings:
        raise HTTPException(422, 'Plan varsayımlarını ve uyarıları onaylayın.')
    db.execute(text('''LOCK TABLE planning_product_settings, planning_orders, planning_raw_orders,
        planning_daily_capacity, planning_weekly_capacity IN SHARE ROW EXCLUSIVE MODE'''))
    _, current = snapshot(db, request, line_id, product_code)
    if current != row.input_fingerprint:
        raise HTTPException(409, 'Stok, sipariş, hammadde veya mevcut plan değişti. Öneriyi yeniden oluşturun.')
    for day in row.result['daily']:
        if day['planned_qty'] > 0:
            conflict = db.execute(text('''SELECT product_code FROM planning_daily_capacity
                WHERE line_id=:lid AND product_code<>:code AND plan_date=:d
                AND planned_qty>0 LIMIT 1'''),
                {'lid': line_id, 'code': product_code, 'd': day['date']}).fetchone()
            if conflict:
                raise HTTPException(409, f"{day['date']}: {conflict[0]} için hat planı var. Aynı günün kapasitesi iki malzemeye ayrı ayrı verilemez.")
        db.execute(text('''INSERT INTO planning_daily_capacity
            (line_id, product_code, plan_date, planned_qty, auto_run_id, shift_code, shift_label,
             shift_hours, is_locked, break_work, break_work_minutes, break_extra_qty)
            VALUES (:lid,:code,:d,:qty,:run,:shift,:label,:hours,:locked,:break_work,:break_minutes,:break_qty)
            ON CONFLICT (line_id, product_code, plan_date) DO UPDATE SET
            planned_qty=EXCLUDED.planned_qty, auto_run_id=EXCLUDED.auto_run_id,
            shift_code=EXCLUDED.shift_code, shift_label=EXCLUDED.shift_label,
            shift_hours=EXCLUDED.shift_hours, is_locked=EXCLUDED.is_locked,
            break_work=EXCLUDED.break_work, break_work_minutes=EXCLUDED.break_work_minutes,
            break_extra_qty=EXCLUDED.break_extra_qty, updated_at=NOW()'''),
            {'lid': line_id, 'code': product_code, 'd': day['date'], 'qty': day['planned_qty'],
             'run': str(run_id), 'shift': day['code'], 'label': day['label'],
             'hours': day['hours'], 'locked': day['locked'],
             'break_work': day['break_work'], 'break_minutes': day['break_minutes'],
             'break_qty': day['break_extra_qty']})
    db.execute(text("UPDATE planning_auto_runs SET status='approved', approved_at=NOW() WHERE run_id=:id"),
               {'id': str(run_id)})
    db.commit()
    return {'status': 'approved', 'updated': len(row.result['daily'])}


@router.get('/approved-days')
def approved_days(start_date: date, end_date: date, line_id: int = Query(2),
                  product_code: str = Query('2962070700'), db: Session = Depends(get_db)):
    scope(line_id, product_code)
    if end_date < start_date or (end_date-start_date).days > 366:
        raise HTTPException(422, 'Tarih aralığı 1–367 gün olmalı.')
    from routers.planning import _product_meta
    material_id = _product_meta(db, product_code)['material']['material_id']
    p = {'lid': line_id, 'code': product_code, 'start': start_date, 'end': end_date}
    rows = db.execute(text('''SELECT plan_date, planned_qty, shift_label, shift_code, shift_hours, is_locked,
        break_work, break_work_minutes, break_extra_qty
        FROM planning_daily_capacity WHERE line_id=:lid AND product_code=:code
        AND plan_date BETWEEN :start AND :end AND auto_run_id IS NOT NULL ORDER BY plan_date'''), p).fetchall()
    from services.material_service import report
    actual, uncertain = {}, set()
    cursor = start_date
    while cursor <= end_date:
        chunk_end = min(end_date, cursor + timedelta(days=92))
        for r in report(db, line_id, cursor, chunk_end, material_id)['items']:
            if r['good'] is None or r['source'] != 'özet':
                uncertain.add(r['date'])
            else:
                actual[r['date']] = actual.get(r['date'], 0) + r['good']
        cursor = chunk_end + timedelta(days=1)
    items = []
    for r in rows:
        item = dict(r._mapping)
        d = str(r.plan_date)
        # Recorded shift summaries only; missing summaries are not inferred as zero.
        qty = actual.get(d) if d not in uncertain and r.plan_date < today() else None
        item.update(actual_good=qty, remaining_qty=max(0, r.planned_qty-qty) if qty is not None else None)
        items.append(item)
    return {'items': items, 'actual_basis': 'Seçilen malzemenin özetlerdeki üretimi eksi malzemeye atanmış fire. Bugün, belirsiz fire ve eksik özetler — gösterilir; eksik kayıt 0 sayılmaz. Tüm vardiya kayıtları tamamlanmamış olabilir.'}
