"""Local, dependency-free single-product daily scheduling and stock simulation.

Start at maximum allowed capacity, then remove hours and excess production
without worsening the attainable daily service/safety profile. This deterministic
heuristic does not claim a global cost optimum. Historical profiles separate
configured-break output from ordinary shift capacity so it is not counted twice.
Every selected workday has at least the day8 hours and its full production target.
"""
from datetime import date, timedelta
from decimal import Decimal, ROUND_FLOOR
import math


def capacity_history(rows, model_id):
    usable, excluded = [], 0
    for r in rows:
        detail = r.get('ideal_cycle_detail') or []
        active = {x.get('model_id') for x in detail if float(x.get('produced') or 0) > 0}
        seconds = int(r.get('planned_base_sec') or 0)
        good = r.get('total_good')
        produced = r.get('total_produced')
        if (active != {model_id} or seconds <= 0 or good is None or produced is None
                or good <= 0 or produced <= 0 or good > produced or r.get('model_id') != model_id):
            excluded += 1
            continue
        hours = seconds / 3600
        rounded_hours = min((8, 10, 12, 16), key=lambda value: abs(value-hours))
        if abs(rounded_hours-hours) > .51:
            excluded += 1
            continue
        break_good = min(max(int(r.get('break_good') or 0), 0), int(good))
        usable.append({
            'good': int(good), 'seconds': seconds, 'date': r['shift_date'],
            'hours': rounded_hours, 'base_good': int(good)-break_good,
            'break_good': break_good,
            'break_minutes': max(int(r.get('break_minutes') or 0), 0),
        })
    rates = sorted(x['good'] * 3600 / x['seconds'] for x in usable)

    def lower_quarter(values):
        ordered = sorted(values)
        return ordered[math.floor((len(ordered)-1)*.25)] if ordered else None

    profiles = {}
    for hours, code in ((8, 'day8'), (10, 'day10'), (12, 'day12'), (16, 'double')):
        group = sorted(
            (x for x in usable if x['hours'] == hours),
            key=lambda x: (x['date'], x['good']), reverse=True,
        )[:20]
        if not group:
            continue
        recent = group[:10]
        top = sorted(group, key=lambda x: (x['base_good'], x['date']), reverse=True)[:5]
        break_group = [x for x in group if x['break_good'] > 0]
        break_recent = break_group[:10]
        break_top = sorted(break_group, key=lambda x: x['break_good'], reverse=True)[:5]

        def average(items, field):
            return round(sum(x[field] for x in items)/len(items)) if items else 0

        profiles[code] = {
            'hours': hours,
            'sample_count': len(group),
            'break_sample_count': len(break_group),
            'first_date': str(min(x['date'] for x in group)),
            'last_date': str(max(x['date'] for x in group)),
            'break_minutes': max((x['break_minutes'] for x in group), default=0),
            'technical_base_capacity': average(top, 'base_good'),
            'normal_base_capacity': average(recent, 'base_good'),
            'cautious_base_capacity': round(lower_quarter([x['base_good'] for x in recent]) or 0),
            'technical_break_extra': average(break_top, 'break_good'),
            'normal_break_extra': average(break_recent, 'break_good'),
            'cautious_break_extra': round(lower_quarter([x['break_good'] for x in break_recent]) or 0),
        }
    return {
        'sample_count': len(usable), 'excluded_count': excluded,
        'gross_good_per_hour': round(sum(x['good'] for x in usable) * 3600 /
                                     sum(x['seconds'] for x in usable), 2) if usable else None,
        'cautious_good_per_hour': round(rates[math.floor((len(rates)-1)*.25)], 2) if rates else None,
        'first_date': str(min(x['date'] for x in usable)) if usable else None,
        'last_date': str(max(x['date'] for x in usable)) if usable else None,
        'profiles': profiles,
        'basis': ('Aynı süreli son 20 vardiya; teknik değer en yüksek 5 gündür. '
                  'Mola dışı temel üretim ve dönüşümlü mola katkısı ayrı hesaplanır.'),
    }


def schedule(inputs, options, locks, *, optimize=True, mode="balanced_weekly"):
    """Inputs are canonical JSON-compatible snapshot values, never DB objects."""
    start = date.fromisoformat(inputs['start_date'])
    days = [str(start + timedelta(days=i)) for i in range(inputs['days'])]
    safety = int(inputs['finished_safety_stock'])
    unit = Decimal(str(inputs['raw_per_piece'])) * (1 + Decimal(str(inputs['raw_scrap_pct']))/100)
    reserve = Decimal(str(inputs['raw_safety_stock']))
    normal_options = []
    option_variants = []
    for source in options:
        extra = int(source.get('break_extra_capacity') or 0)
        minutes = int(source.get('break_minutes') or 0)
        if extra < 0 or minutes < 0 or (extra > 0) != (minutes > 0):
            raise ValueError('Mola katkısı ve mola süresi birlikte, sıfır veya pozitif girilmelidir.')
        normal = dict(source)
        normal.update(
            normal_capacity=int(source['capacity']), break_work=False,
            break_extra_capacity=0, break_minutes=0,
            option_key=source['code'],
        )
        normal_options.append(normal)
        option_variants.append(normal)
        if extra > 0 and minutes > 0:
            worked = dict(normal)
            worked.update(
                capacity=normal['capacity']+extra,
                break_work=True, break_extra_capacity=extra,
                break_minutes=minutes,
                option_key=f"{source['code']}_break",
                label=f"{source['label']} · dönüşümlü mola",
            )
            option_variants.append(worked)
    by_code = {(x['code'], x['break_work']): x for x in option_variants}
    base = next((x for x in normal_options if x['code'] == 'day8'), None)
    if base is None or base['capacity'] <= 0 or base['hours'] != 8:
        raise ValueError('08.00–16.00 seçeneği ve sıfırdan büyük sağlam adet kapasitesi zorunludur.')
    minimum_qty = base['capacity']
    if any(x['hours'] < 8 or x['capacity'] < minimum_qty for x in normal_options):
        raise ValueError('Açık çalışma seçenekleri 8 saatten ve temel günlük kapasiteden düşük olamaz.')
    def is_workday(d):
        return date.fromisoformat(d).weekday() in inputs['workdays']
    idle = {'code': 'off', 'label': 'Takvimde kapalı', 'hours': 0, 'capacity': 0}
    idle.update(normal_capacity=0, break_work=False, break_extra_capacity=0,
                break_minutes=0, option_key='off')
    choices = sorted([idle] + option_variants,
                     key=lambda x: (x['hours'], x['break_work'], x['capacity'], x['code']))
    lock_map = {x['date']: x for x in locks}
    if len(lock_map) != len(locks) or any(d not in days for d in lock_map):
        raise ValueError('Sabit günler dönem içinde ve tekil olmalı.')
    for lock in locks:
        opt = by_code.get((lock['code'], bool(lock.get('break_work'))))
        if opt is None or not 0 <= lock['qty'] <= opt['capacity']:
            raise ValueError('Sabit gün adedi seçilen çalışma kapasitesini aşıyor veya seçenek kapalı.')
        if is_workday(lock['date']) and (lock['code'] == 'off' or lock['qty'] < minimum_qty):
            raise ValueError('Çalışma günleri en az 08.00–16.00 ve temel günlük üretim adediyle sabitlenebilir.')
        if not is_workday(lock['date']) and lock['code'] != 'off':
            raise ValueError('Kapalı çalışma gününe üretim sabitlenemez; önce çalışma gününü açın.')
    selected = {}
    for d in days:
        if d in lock_map:
            lock = lock_map[d]
            selected[d] = (by_code[(lock['code'], bool(lock.get('break_work')))], lock['qty'])
        elif date.fromisoformat(d).weekday() in inputs['workdays']:
            best = max(choices, key=lambda x: (x['capacity'], -x['hours']))
            selected[d] = (best, best['capacity'])
        else:
            selected[d] = (idle, 0)

    def simulate():
        fg = int(inputs['finished_stock'])
        raw = Decimal(str(inputs['raw_stock_qty']))
        result = []
        for d in days:
            option, qty = selected[d]
            receipt = Decimal(str(inputs['receipts'].get(d, 0)))
            raw += receipt
            feasible = qty if unit <= 0 else min(qty, max(0, int(((raw-reserve)/unit).to_integral_value(rounding=ROUND_FLOOR))))
            opening = fg
            order = int(inputs['orders'].get(d, 0))
            # Morning dispatch uses opening stock; today's production cannot undo a missed dispatch.
            shortage = max(0, order-opening) if inputs['dispatch_timing'] == 'before' else max(0, order-opening-feasible)
            fg += feasible - order
            raw -= unit*feasible
            result.append({
                'date': d, 'code': option['code'], 'label': option['label'],
                'minimum_closing_stock': safety, 'stock_margin': fg-safety,
                'option_key': option['option_key'], 'break_work': option['break_work'],
                'break_minutes': option['break_minutes'],
                'break_extra_capacity': option['break_extra_capacity'],
                'break_extra_qty': max(0, qty-option['normal_capacity']),
                'hours': option['hours'], 'capacity': option['capacity'],
                'planned_qty': qty, 'feasible_qty': feasible,
                'order_qty': order, 'opening_finished_stock': opening,
                'closing_finished_stock': fg, 'shortage_qty': shortage,
                'backlog_qty': max(0, -fg), 'safety_shortage_qty': max(0, safety-fg),
                'raw_receipt_qty': float(receipt), 'closing_raw_stock': round(float(raw), 3),
                'raw_limited_qty': qty-feasible, 'locked': d in lock_map,
            })
        return result

    maximum = simulate()
    def acceptable(trial):
        return all(r['shortage_qty'] <= m['shortage_qty'] and
                   r['safety_shortage_qty'] <= m['safety_shortage_qty']
                   for r, m in zip(trial, maximum))

    if optimize:
        for d in days:
            if d in lock_map:
                continue
            original = selected[d]
            for opt in choices:
                if is_workday(d) and opt['code'] == 'off':
                    continue
                if opt['hours'] > original[0]['hours']:
                    continue
                if date.fromisoformat(d).weekday() not in inputs['workdays'] and opt['code'] != 'off':
                    continue
                selected[d] = (opt, opt['capacity'])
                if acceptable(simulate()):
                    break
            else:
                selected[d] = original
        # Trim unused output. Do not turn an excess-stock reduction into a new capacity restriction.
        for d in days:
            if d in lock_map:
                continue
            opt, hi = selected[d]
            lo = minimum_qty if is_workday(d) else 0
            while lo < hi:
                mid = (lo+hi)//2
                selected[d] = (opt, mid)
                if acceptable(simulate()):
                    hi = mid
                else:
                    lo = mid+1
            fitting = [x for x in choices if x['capacity'] >= lo and x['hours'] <= opt['hours']]
            selected[d] = (min(fitting, key=lambda x: (x['hours'], x['break_work'], x['capacity'])), lo)
    result = simulate()
    flexible_selected = dict(selected)

    def plan_metrics(rows):
        work_rows = [row for row in rows if row["hours"] > 0]
        changes = 0
        previous_code = None
        previous_week = None
        for row in work_rows:
            parsed = date.fromisoformat(row["date"])
            week = (parsed.isocalendar().year, parsed.isocalendar().week)
            if previous_code is not None and week == previous_week and row["option_key"] != previous_code:
                changes += 1
            previous_code = row["option_key"]
            previous_week = week
        return {
            "total_hours": sum(row["hours"] for row in work_rows),
            "long_day_count": sum(row["hours"] >= 12 for row in work_rows),
            "double_shift_day_count": sum(row["code"] == "double" for row in work_rows),
            "break_work_day_count": sum(row["break_work"] for row in work_rows),
            "break_work_minutes": sum(row["break_minutes"] for row in work_rows if row["break_work"]),
            "break_extra_qty": sum(row["break_extra_qty"] for row in work_rows),
            "shift_change_count": changes,
            "total_planned": sum(row["planned_qty"] for row in rows),
            "total_feasible": sum(row["feasible_qty"] for row in rows),
            "closing_stock": rows[-1]["closing_finished_stock"],
            "minimum_stock": min(row["closing_finished_stock"] for row in rows),
            "max_shortage": max(row["shortage_qty"] for row in rows),
        }

    def balance_score(rows):
        metrics = plan_metrics(rows)
        long_burden = sum(max(0, row["hours"] - 10) ** 2 for row in rows)
        return (
            metrics["total_hours"],
            metrics["break_work_minutes"],
            long_burden,
            metrics["double_shift_day_count"],
            metrics["shift_change_count"],
            metrics["total_planned"],
        )

    if mode not in {"balanced_weekly", "daily_flexible"}:
        raise ValueError("Planlama modu balanced_weekly veya daily_flexible olmalı.")

    flexible_rows = result
    if optimize:
        weeks = []
        for d in days:
            parsed = date.fromisoformat(d)
            key = (parsed.isocalendar().year, parsed.isocalendar().week)
            if key not in weeks:
                weeks.append(key)
        # Three bounded passes test single days and day pairs. This catches
        # useful exchanges such as 12+8 -> 10+10 without an exponential
        # search. Every candidate is still checked against the full horizon.
        for week_order in (weeks, list(reversed(weeks)), weeks):
            for week in week_order:
                week_days = [
                    d for d in days
                    if (
                        date.fromisoformat(d).isocalendar().year,
                        date.fromisoformat(d).isocalendar().week,
                    ) == week and is_workday(d) and d not in lock_map
                ]
                if not week_days:
                    continue
                original_selected = {d: selected[d] for d in week_days}
                best_selected = dict(original_selected)
                best_rows = simulate()
                best_score = balance_score(best_rows)

                def consider(changes):
                    nonlocal best_selected, best_rows, best_score
                    for d, value in original_selected.items():
                        selected[d] = value
                    for d, opt in changes.items():
                        selected[d] = (opt, opt["capacity"])
                    candidate = simulate()
                    score = balance_score(candidate)
                    if acceptable(candidate) and score < best_score:
                        best_selected = {d: selected[d] for d in week_days}
                        best_rows = candidate
                        best_score = score

                for d in week_days:
                    for opt in option_variants:
                        consider({d: opt})
                for index, first in enumerate(week_days):
                    for second in week_days[index + 1:]:
                        for first_opt in option_variants:
                            for second_opt in option_variants:
                                consider({first: first_opt, second: second_opt})
                for d, value in best_selected.items():
                    selected[d] = value
                result = best_rows
        # Once the paid-hour pattern is chosen, avoid producing pieces that
        # are not needed for the attainable delivery/safety profile.
        for d in days:
            if d in lock_map or not is_workday(d):
                continue
            opt, hi = selected[d]
            lo = minimum_qty
            while lo < hi:
                mid = (lo + hi) // 2
                selected[d] = (opt, mid)
                if acceptable(simulate()):
                    hi = mid
                else:
                    lo = mid + 1
            fitting = [x for x in option_variants if x['capacity'] >= lo and x['hours'] <= opt['hours']]
            selected[d] = (min(fitting, key=lambda x: (x['hours'], x['break_work'], x['capacity'])), lo)
        result = simulate()
    balanced_rows = result
    if mode == "daily_flexible":
        selected = flexible_selected
        result = simulate()
    warnings = []
    if unit <= 0:
        warnings.append('Hammadde birim tüketimi tanımsız; hammadde yeterliliği doğrulanmadı.')
    if inputs['include_planned']:
        warnings.append('Teyitsiz, planlanan hammadde partileri bu senaryoya dahil.')
    if any(r['shortage_qty'] for r in result):
        warnings.append('Seçili kapasite, malzeme ve sabit günlerle bazı sevkiyatlar karşılanamıyor.')
    if any(r['safety_shortage_qty'] for r in result):
        warnings.append('Bazı günlerde mamul emniyet stokunun altında kalınıyor.')
    if any(r['raw_limited_qty'] for r in result):
        warnings.append('Sabit/günlük üretim hedefinin bir kısmı hammadde nedeniyle üretilemiyor.')
    for r in result:
        r['status'] = ('red' if r['shortage_qty'] else 'yellow' if
                       r['safety_shortage_qty'] or r['raw_limited_qty'] or
                       (unit <= 0) or inputs['include_planned'] else 'green')
    return {
        'policy_version': 'materials_stock_v1',
        'planning_mode': mode,
        'minimum_daily_qty': minimum_qty,
        'daily': result, 'warnings': warnings,
        'option_variants': option_variants,
        'summary': {
            'total_planned': sum(r['planned_qty'] for r in result),
            'total_feasible': sum(r['feasible_qty'] for r in result),
            'total_order': sum(r['order_qty'] for r in result),
            'total_hours': sum(r['hours'] for r in result),
            'first_shortage_date': next((r['date'] for r in result if r['shortage_qty']), None),
            'max_shortage': max(r['shortage_qty'] for r in result),
            'closing_stock': result[-1]['closing_finished_stock'],
        },
        'method': 'Yerel, tek ürünlü sezgisel plan; küresel en düşük maliyet garantisi yoktur.',
        'comparison': {
            'daily_flexible': {
                'label': 'Günlük esnek plan',
                **plan_metrics(flexible_rows),
            },
            'balanced_weekly': {
                'label': 'Haftalık dengeli plan',
                **plan_metrics(balanced_rows),
            },
            'selected': mode,
            'decision_order': [
                'Sipariş ve emniyet stoku',
                'Toplam hat çalışma saati',
                'Dönüşümlü mola çalışması',
                '12 saatlik ve iki vardiyalı günler',
                'Hafta içi düzen değişikliği',
                'Gereksiz üretim ve stok',
            ],
            'cost_warning': (
                'Parasal kârlılık hesaplanmadı; işçilik, fazla mesai, yemek-servis, '
                'enerji ve stok taşıma maliyetleri tanımlı değil.'
            ),
        },
        'assumptions': [
            'Seçili her çalışma gününde en az 08.00–16.00 ve bu sürenin tam üretim kapasitesi planlanır; fazla üretim stoka eklenir.',
            'Mola üretimi temel kapasiteden ayrıdır; yalnızca sipariş veya emniyet stoku için gerektiğinde dönüşümlü ekiple eklenir.',
            'Eşit toplam saatli uygun planlarda daha az 12 saatlik gün ve daha sabit haftalık çalışma düzeni tercih edilir.',
            'Stok miktarları başlangıç gününün üretim ve sevkiyat öncesi sayımıdır.',
            'Hammadde beklenen tarihte üretim başlamadan kullanılabilir kabul edilir.',
            'Hammadde emniyet stoku üretime ayrılmaz.',
            'Saatler hat çalışma planıdır; personel ataması ve vardiya ayarları ayrıca yönetilir.',
        ],
    }
