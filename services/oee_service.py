"""OEE hesaplaması için tek kaynak.

Bu modül üç yerde aynı formülün kullanılmasını sağlar:

* canlı ``/production/summary/now`` yanıtı,
* vardiya sonunda yazılan ``shift_summary`` kaydı,
* geçmiş vardiyaların kontrollü yeniden hesaplanması.

DB bağımlılıkları fonksiyon içinde yüklenir. Böylece saf süre/formül
fonksiyonları üretim sunucusu dışında da kolayca test edilebilir.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Europe/Istanbul")
FORMULA_VERSION = "oee-v3"
BREAK_REASON_CODE = "OPERATOR"


class ActiveDowntimeError(RuntimeError):
    """Bitmiş vardiyada hâlâ açık duruş bulunduğunda yükseltilir."""


class IdealCycleConfigurationError(RuntimeError):
    """Üretime ait güvenilir ideal çevrim bulunamadığında yükseltilir."""


def _as_dict(row):
    if row is None:
        return None
    mapping = getattr(row, "_mapping", None)
    return dict(mapping if mapping is not None else row)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=TZ)
    return value.astimezone(TZ)


def merge_intervals(intervals: Iterable[tuple[datetime, datetime]]):
    """Çakışan zaman aralıklarını birleştirir."""
    ordered = sorted(
        (start, end) for start, end in intervals if end > start
    )
    merged: list[list[datetime]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        elif end > merged[-1][1]:
            merged[-1][1] = end
    return [(start, end) for start, end in merged]


def interval_seconds(intervals: Iterable[tuple[datetime, datetime]]) -> int:
    return int(sum((end - start).total_seconds() for start, end in intervals))


def overlap_seconds(
    left: Iterable[tuple[datetime, datetime]],
    right: Iterable[tuple[datetime, datetime]],
) -> int:
    """İki birleştirilmiş aralık kümesinin kesişim süresini döndürür."""
    left = list(left)
    right = list(right)
    i = j = 0
    total = 0.0
    while i < len(left) and j < len(right):
        start = max(left[i][0], right[j][0])
        end = min(left[i][1], right[j][1])
        if end > start:
            total += (end - start).total_seconds()
        if left[i][1] <= right[j][1]:
            i += 1
        else:
            j += 1
    return int(total)


def downtime_buckets(
    events,
    window_start: datetime,
    window_end: datetime,
    break_windows=(),
):
    """Duruşları vardiya sınırında keser ve çift sayımı engeller.

    Mola tanımı tek başına süre düşürmez. Yalnızca gerçek bir duruş ile mola
    penceresinin kesişimi OEE dışıdır. ``OPERATOR`` kodunun pencere dışında
    kalan kısmı plansız sayılır; böylece uzun mola seçilerek OEE yükseltilemez.
    Diğer açıkça OEE dışı sebepler kendi gerçek süreleri kadar hariç tutulur.
    ``VARDIYA_SONU`` üretim penceresinin dışını temsil ettiği için atlanır.
    """
    from services.break_service import intersect_windows

    all_downtime = []
    explicit_excluded = []
    for event in events:
        event = _as_dict(event)
        if event.get("reason_code") == "VARDIYA_SONU":
            continue
        start = max(_aware(event["started_at"]), window_start)
        ended_at = event.get("ended_at") or window_end
        end = min(_aware(ended_at), window_end)
        if end <= start:
            continue
        all_downtime.append((start, end))
        if (
            event.get("exclude_from_oee")
            and event.get("reason_code") != BREAK_REASON_CODE
        ):
            explicit_excluded.append((start, end))

    all_downtime = merge_intervals(all_downtime)
    clipped_breaks = merge_intervals([
        (max(window_start, start), min(window_end, end))
        for start, end in break_windows
        if min(window_end, end) > max(window_start, start)
    ])
    actual_breaks = intersect_windows(all_downtime, clipped_breaks)
    excluded = merge_intervals(explicit_excluded + actual_breaks)
    excluded_sec = interval_seconds(excluded)
    unplanned_sec = interval_seconds(all_downtime) - overlap_seconds(
        all_downtime,
        excluded,
    )
    return max(excluded_sec, 0), max(unplanned_sec, 0)


def calculate_factors(
    *,
    planned_base_sec: int,
    excluded_sec: int,
    unplanned_sec: int,
    produced: int,
    scrap: int,
    ideal_cycle_sec: float,
    ideal_time_sec: float | None = None,
):
    """OEE ve bileşenlerini yüzde olarak hesaplar.

    Formüller:

    * Planlanan üretim = vardiya tabanı − OEE dışı süre
    * Çalışma = planlanan üretim − plansız duruş
    * Availability = çalışma / planlanan üretim
    * Performance = (üretim × ideal çevrim) / çalışma
    * Quality = iyi üretim / toplam üretim
    * OEE = Availability × Performance × Quality
    """
    planned_base_sec = max(int(planned_base_sec or 0), 0)
    excluded_sec = min(max(int(excluded_sec or 0), 0), planned_base_sec)
    planned_sec = max(planned_base_sec - excluded_sec, 0)
    unplanned_sec = min(max(int(unplanned_sec or 0), 0), planned_sec)
    run_time_sec = max(planned_sec - unplanned_sec, 0)
    produced = max(int(produced or 0), 0)
    scrap = max(int(scrap or 0), 0)
    good = max(produced - scrap, 0)
    ideal_cycle_sec = max(float(ideal_cycle_sec or 0), 0.0)
    if ideal_time_sec is None:
        ideal_time_sec = produced * ideal_cycle_sec
    ideal_time_sec = max(float(ideal_time_sec or 0), 0.0)

    availability = (
        min(run_time_sec / planned_sec, 1.0) if planned_sec > 0 else 0.0
    )
    performance = (
        min(ideal_time_sec / run_time_sec, 1.0) if run_time_sec > 0 else 0.0
    )
    quality = good / produced if produced > 0 else 1.0
    overall = availability * performance * quality

    return {
        "planned_base_sec": planned_base_sec,
        "excluded_sec": excluded_sec,
        "planned_production_sec": planned_sec,
        "unplanned_sec": unplanned_sec,
        "run_time_sec": run_time_sec,
        "produced": produced,
        "scrap": scrap,
        "good": good,
        "ideal_cycle_sec": round(ideal_cycle_sec, 3),
        "ideal_time_sec": round(ideal_time_sec, 3),
        "availability": round(availability * 100, 1),
        "performance": round(performance * 100, 1),
        "quality": round(quality * 100, 1),
        "overall": round(overall * 100, 1),
    }


def aggregate_oee(rows):
    """Vardiya sonuçlarını süre/adet ağırlıklı tek OEE sonucunda birleştirir."""
    rows = list(rows)
    planned = sum(float(row.get("planned_production_sec") or 0) for row in rows)
    run_time = sum(float(row.get("run_time_sec") or 0) for row in rows)
    ideal_time = sum(float(row.get("ideal_time_sec") or 0) for row in rows)
    produced = sum(int(row.get("total_produced", row.get("produced", 0)) or 0) for row in rows)
    scrap = sum(int(row.get("total_scrap", row.get("scrap", 0)) or 0) for row in rows)
    good = sum(int(row.get("total_good", row.get("good", 0)) or 0) for row in rows)

    availability = min(run_time / planned, 1.0) if planned > 0 else 0.0
    performance = min(ideal_time / run_time, 1.0) if run_time > 0 else 0.0
    quality = good / produced if produced > 0 else 1.0
    overall = availability * performance * quality
    weighted_ideal_cycle = ideal_time / produced if produced > 0 else 0.0
    return {
        "availability": round(availability * 100, 1),
        "performance": round(performance * 100, 1),
        "quality": round(quality * 100, 1),
        "overall": round(overall * 100, 1),
        "planned_production_sec": int(planned),
        "run_time_sec": int(run_time),
        "ideal_time_sec": round(ideal_time, 3),
        "ideal_cycle_sec": round(weighted_ideal_cycle, 3),
        "theoretical_output": int(planned / weighted_ideal_cycle)
        if weighted_ideal_cycle > 0 else 0,
        "total_produced": produced,
        "total_scrap": scrap,
        "total_good": good,
        "summary_count": len(rows),
    }


def shift_bounds(shift: dict, shift_date: date):
    start = datetime.combine(shift_date, datetime.min.time(), tzinfo=TZ)
    start += timedelta(hours=int(shift["start_hour"]))
    end = datetime.combine(shift_date, datetime.min.time(), tzinfo=TZ)
    end += timedelta(hours=int(shift["end_hour"]))
    if end <= start:
        end += timedelta(days=1)
    return start, end


def calculate_shift_summary(
    db,
    *,
    line_id: int,
    shift_date: date,
    shift_code: str,
    as_of: datetime | None = None,
    require_complete: bool = True,
):
    """Ham üretim/fire/duruş kayıtlarından bir vardiya sonucunu hesaplar."""
    from sqlalchemy import text
    import shift_utils

    shifts = shift_utils.get_shifts(line_id=line_id, dt=shift_date)
    shift = next((item for item in shifts if item["code"] == shift_code), None)
    if shift is None:
        raise ValueError(f"Vardiya bulunamadı: {shift_code} / {shift_date}")

    start_at, end_at = shift_bounds(shift, shift_date)
    as_of = _aware(as_of or datetime.now(TZ))
    if require_complete and as_of < end_at:
        raise ValueError(f"Vardiya henüz bitmedi: {shift_code} / {shift_date}")
    effective_end = end_at if require_complete else min(as_of, end_at)
    if effective_end <= start_at:
        elapsed_base = 0
    else:
        elapsed_base = min(
            int((effective_end - start_at).total_seconds()),
            int(shift["planned_seconds"]),
        )

    production = _as_dict(db.execute(text("""
        WITH ordered AS (
            SELECT logged_at, produced, average_cycle,
                   LAG(produced) OVER (ORDER BY logged_at, log_id) AS previous_produced
            FROM production_log
            WHERE line_id=:lid
              AND shift=:shift
              AND logged_at >= :start_at
              AND logged_at < :end_at
        )
        SELECT MIN(logged_at) AS first_at,
               MAX(logged_at) AS last_at,
               COALESCE(SUM(
                   CASE
                     WHEN previous_produced IS NULL THEN 0
                     WHEN produced >= previous_produced THEN produced - previous_produced
                     ELSE GREATEST(produced, 0)
                   END
               ), 0) AS shift_produced,
               COALESCE(AVG(NULLIF(average_cycle, 0)), 0) AS avg_cycle
        FROM ordered
    """), {
        "lid": line_id,
        "shift": shift_code,
        "start_at": start_at,
        "end_at": effective_end,
    }).fetchone())

    produced_by_model_rows = db.execute(text("""
        WITH ordered AS (
            SELECT model_id, produced,
                   LAG(produced) OVER (ORDER BY logged_at, log_id) AS previous_produced
            FROM production_log
            WHERE line_id=:lid
              AND shift=:shift
              AND logged_at >= :start_at
              AND logged_at < :end_at
        ), deltas AS (
            SELECT model_id,
                   CASE
                     WHEN previous_produced IS NULL THEN 0
                     WHEN produced >= previous_produced THEN produced - previous_produced
                     ELSE GREATEST(produced, 0)
                   END AS produced_delta
            FROM ordered
        )
        SELECT model_id, COALESCE(SUM(produced_delta), 0) AS produced
        FROM deltas
        GROUP BY model_id
        HAVING COALESCE(SUM(produced_delta), 0) > 0
        ORDER BY model_id NULLS FIRST
    """), {
        "lid": line_id,
        "shift": shift_code,
        "start_at": start_at,
        "end_at": effective_end,
    }).fetchall()

    latest = _as_dict(db.execute(text("""
        SELECT model_id, target
        FROM production_log
        WHERE line_id=:lid
          AND shift=:shift
          AND logged_at >= :start_at
          AND logged_at < :end_at
        ORDER BY logged_at DESC, log_id DESC
        LIMIT 1
    """), {
        "lid": line_id,
        "shift": shift_code,
        "start_at": start_at,
        "end_at": effective_end,
    }).fetchone()) or {}

    scrap_row = db.execute(text("""
        SELECT COALESCE(SUM(qty), 0)
        FROM scrap_entries
        WHERE line_id=:lid
          AND created_at >= :start_at
          AND created_at < :end_at
    """), {
        "lid": line_id,
        "start_at": start_at,
        "end_at": effective_end,
    }).fetchone()
    scrap = int(scrap_row[0] if scrap_row else 0)

    downtime_rows = db.execute(text("""
        SELECT d.started_at, d.ended_at, d.is_active,
               r.reason_code, COALESCE(r.exclude_from_oee, FALSE) AS exclude_from_oee
        FROM downtimes d
        JOIN downtime_reasons r ON r.reason_id=d.reason_id
        WHERE d.line_id=:lid
          AND d.started_at < :end_at
          AND COALESCE(d.ended_at, :end_at) > :start_at
        ORDER BY d.started_at
    """), {
        "lid": line_id,
        "start_at": start_at,
        "end_at": effective_end,
    }).fetchall()

    if require_complete and any(_as_dict(row).get("is_active") for row in downtime_rows):
        raise ActiveDowntimeError(
            f"Aktif duruş var: line={line_id} shift={shift_code} date={shift_date}"
        )
    excluded_sec, unplanned_sec = downtime_buckets(
        downtime_rows,
        start_at,
        effective_end,
        break_windows=shift_utils.break_windows(
            shift_code,
            shift_date,
            line_id=line_id,
            tzinfo=TZ,
        ),
    )

    line_row = db.execute(text("""
        SELECT ideal_cycle_ds FROM production_lines WHERE line_id=:lid
    """), {"lid": line_id}).fetchone()
    avg_cycle = float((production or {}).get("avg_cycle") or 0)
    produced = int((production or {}).get("shift_produced") or 0)
    line_default_ds = int(line_row[0]) if line_row and line_row[0] else None

    material_rows = db.execute(text("""
        SELECT model_id, ideal_cycle_ds
        FROM materials
        WHERE model_id IS NOT NULL
          AND ideal_cycle_ds IS NOT NULL
    """)).fetchall()
    cycles_by_model = {}
    for model_id, ideal_cycle_ds in material_rows:
        cycles_by_model.setdefault(model_id, set()).add(int(ideal_cycle_ds))

    ideal_detail = []
    ideal_time_sec = 0.0
    for row in produced_by_model_rows:
        model_id = row[0]
        model_produced = int(row[1] or 0)
        configured = cycles_by_model.get(model_id, set())
        if len(configured) > 1:
            raise IdealCycleConfigurationError(
                f"model_id={model_id} için birden fazla ideal çevrim var: "
                f"{sorted(configured)}"
            )
        if configured:
            ideal_ds = next(iter(configured))
            source = "material"
        elif line_default_ds:
            ideal_ds = line_default_ds
            source = "line_default"
        else:
            raise IdealCycleConfigurationError(
                f"İdeal çevrim eksik: line={line_id} model_id={model_id}"
            )
        cycle_sec = ideal_ds / 10.0
        ideal_time_sec += model_produced * cycle_sec
        ideal_detail.append({
            "model_id": model_id,
            "produced": model_produced,
            "ideal_cycle_sec": round(cycle_sec, 3),
            "source": source,
        })

    ideal_cycle_sec = ideal_time_sec / produced if produced > 0 else 0.0
    factors = calculate_factors(
        planned_base_sec=elapsed_base,
        excluded_sec=excluded_sec,
        unplanned_sec=unplanned_sec,
        produced=produced,
        scrap=scrap,
        ideal_cycle_sec=ideal_cycle_sec,
        ideal_time_sec=ideal_time_sec,
    )

    return {
        "formula_version": FORMULA_VERSION,
        "line_id": line_id,
        "shift_date": shift_date,
        "shift": shift_code,
        "model_id": latest.get("model_id"),
        "target": int(latest.get("target") or 0),
        "total_produced": produced,
        "total_scrap": factors["scrap"],
        "total_good": factors["good"],
        "avg_cycle_time": round(avg_cycle, 3),
        "total_downtime_sec": factors["unplanned_sec"],
        "break_sec": factors["excluded_sec"],
        "oee_availability": factors["availability"],
        "oee_performance": factors["performance"],
        "oee_quality": factors["quality"],
        "oee_overall": factors["overall"],
        "first_cycle_at": (production or {}).get("first_at"),
        "last_cycle_at": (production or {}).get("last_at"),
        "planned_base_sec": factors["planned_base_sec"],
        "planned_production_sec": factors["planned_production_sec"],
        "run_time_sec": factors["run_time_sec"],
        "ideal_cycle_sec": factors["ideal_cycle_sec"],
        "ideal_time_sec": factors["ideal_time_sec"],
        "ideal_cycle_detail": ideal_detail,
        "theoretical_output": int(
            factors["planned_production_sec"] / ideal_cycle_sec
        ) if ideal_cycle_sec > 0 else 0,
        "is_complete": effective_end >= end_at,
    }


def upsert_shift_summary(db, result: dict, *, commit: bool = True):
    """Hesaplanan sonucu mevcut tekil vardiya kaydına kaybı olmadan yazar."""
    from sqlalchemy import text

    db.execute(text("""
        INSERT INTO shift_summary
            (line_id, shift_date, shift, model_id, target,
             total_produced, total_scrap, total_good, avg_cycle_time,
             total_downtime_sec, break_sec, oee_availability,
             oee_performance, oee_quality, oee_overall,
             first_cycle_at, last_cycle_at, formula_version, calculated_at,
             planned_base_sec, planned_production_sec, run_time_sec,
             ideal_cycle_sec, ideal_time_sec, ideal_cycle_detail)
        VALUES
            (:line_id,:shift_date,:shift,:model_id,:target,
             :total_produced,:total_scrap,:total_good,:avg_cycle_time,
             :total_downtime_sec,:break_sec,:oee_availability,
             :oee_performance,:oee_quality,:oee_overall,
             :first_cycle_at,:last_cycle_at,:formula_version,NOW(),
             :planned_base_sec,:planned_production_sec,:run_time_sec,
             :ideal_cycle_sec,:ideal_time_sec,
             CAST(:ideal_cycle_detail_json AS JSONB))
        ON CONFLICT (line_id, shift_date, shift) DO UPDATE SET
            model_id=EXCLUDED.model_id,
            target=EXCLUDED.target,
            total_produced=EXCLUDED.total_produced,
            total_scrap=EXCLUDED.total_scrap,
            total_good=EXCLUDED.total_good,
            avg_cycle_time=EXCLUDED.avg_cycle_time,
            total_downtime_sec=EXCLUDED.total_downtime_sec,
            break_sec=EXCLUDED.break_sec,
            oee_availability=EXCLUDED.oee_availability,
            oee_performance=EXCLUDED.oee_performance,
            oee_quality=EXCLUDED.oee_quality,
            oee_overall=EXCLUDED.oee_overall,
            first_cycle_at=EXCLUDED.first_cycle_at,
            last_cycle_at=EXCLUDED.last_cycle_at,
            formula_version=EXCLUDED.formula_version,
            calculated_at=NOW(),
            planned_base_sec=EXCLUDED.planned_base_sec,
            planned_production_sec=EXCLUDED.planned_production_sec,
            run_time_sec=EXCLUDED.run_time_sec,
            ideal_cycle_sec=EXCLUDED.ideal_cycle_sec,
            ideal_time_sec=EXCLUDED.ideal_time_sec,
            ideal_cycle_detail=EXCLUDED.ideal_cycle_detail
    """), {
        **result,
        "ideal_cycle_detail_json": json.dumps(
            result.get("ideal_cycle_detail") or [],
            ensure_ascii=False,
        ),
    })
    if commit:
        db.commit()
