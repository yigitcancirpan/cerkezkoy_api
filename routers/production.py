"""
Üretim Takip API Router — v2
routers/production.py

Yeni endpoint'ler:
  GET  /api/v1/production/current/{line_id}  → Anlık + ilk/son baskı + ETA
  POST /api/v1/production/summary/now        → Manuel vardiya özeti hesapla
  GET  /api/v1/production/shifts             → Vardiya özetleri
  GET  /api/v1/production/daily              → Günlük özet
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
from datetime import date, datetime, timedelta

from models.database import get_db
from services.oee_service import (
    FORMULA_VERSION,
    aggregate_oee,
    calculate_shift_summary,
)

router = APIRouter(prefix="/api/v1/production", tags=["Üretim Takip"])


def _calc_eta(produced, target, average_cycle, last_cycle_at=None):
    """
    Tahmini bitiş saati — cycle time bazlı (her zaman doğru)

    average_cycle: PLC'den gelen ortalama cycle süresi (saniyenin 10'da biri)
    Örnek: 90 = 9.0 saniye/parça → 400 adet/saat
    """
    if not produced or not target or produced <= 0 or target <= produced:
        return None
    if not average_cycle or average_cycle <= 0:
        return None

    cycle_sec = average_cycle / 10.0  # 90 → 9.0 saniye
    rate_per_hour = 3600 / cycle_sec  # 3600 / 9.0 = 400 adet/saat
    remaining = target - produced
    remaining_sec = remaining * cycle_sec

    now = datetime.now()
    eta = now + timedelta(seconds=remaining_sec)

    return {
        "eta": eta.strftime("%H:%M"),
        "remaining_pieces": remaining,
        "remaining_minutes": round(remaining_sec / 60),
        "rate_per_hour": round(rate_per_hour),
        "cycle_sec": round(cycle_sec, 1),
    }


@router.get("/current/{line_id}")
def get_current(line_id: int, db: Session = Depends(get_db)):
    """Anlık üretim + ilk/son baskı + tahmini bitiş"""
    row = db.execute(text("""
        SELECT line_id, lot_number, model_id, target, produced, scrap, good,
               actual_cycle, average_cycle, last_5_cycles,
               first_cycle_at, last_cycle_at, shift_start_produced,
               current_shift, cycle_running, auto_cycle_on, cycle_running_changed_at, updated_at
        FROM production_current
        WHERE line_id = :lid
    """), {"lid": line_id}).fetchone()

    if not row:
        return {"produced": 0, "target": 0}

    result = dict(row._mapping)
    # Sık sorgulanan dashboard için hafif vardiya üretim tahmini.
    # Kesin OEE/özet hesabı sayaç resetlerini de işleyen oee_service içindedir.
    sc = result.get("current_shift")
    if sc:
        pr = db.execute(text("""
            SELECT COALESCE(MAX(produced) - MIN(produced), 0)
            FROM production_log
            WHERE line_id = :lid AND logged_at::date = CURRENT_DATE AND shift = :shift
        """), {"lid": line_id, "shift": sc}).fetchone()
        result["produced_shift"] = int(pr[0]) if pr else 0
    else:
        result["produced_shift"] = result.get("produced", 0)
    # ── İLK BASKI: snapshot DEĞİL, production_log'dan türet (restart-proof) ──
    # production_log append-only ve kalıcı; restart öncesi kayıtlar tabloda kalır,
    # bu yüzden MIN(logged_at) restart'tan etkilenmez.
    shift_code = result.get("current_shift")
    if shift_code:
        first_row = db.execute(text("""
            SELECT MIN(logged_at) AS first_at
            FROM production_log
            WHERE line_id = :lid AND logged_at::date = CURRENT_DATE AND shift = :shift
        """), {"lid": line_id, "shift": shift_code}).fetchone()
    else:
        first_row = db.execute(text("""
            SELECT MIN(logged_at) AS first_at
            FROM production_log
            WHERE line_id = :lid AND logged_at::date = CURRENT_DATE
        """), {"lid": line_id}).fetchone()

    if first_row and first_row[0]:
        result["first_cycle_at"] = first_row[0]   # snapshot'ı override et

    # Vardiya içi dashboard değeri: production_log MAX−MIN (yukarıda hesaplandı)
    # shift_produced, produced_shift'in takma adı (frontend/terminal bu ismi kullanıyor)
    result["shift_produced"] = result["produced_shift"]

    eta = _calc_eta(result["produced_shift"], result.get("target"), result.get("average_cycle"))
    result["eta"] = eta

    return result


@router.post("/summary/now")
def calculate_summary_now(
    line_id: int = Query(1),
    db: Session = Depends(get_db),
):
    """Manuel vardiya özeti — şu ana kadar olan verilerle hesapla"""
    import shift_utils
    now = datetime.now().astimezone()
    shift_code = shift_utils.detect_shift_code(now, line_id=line_id)
    shift = shift_utils.shift_by_code(shift_code, line_id=line_id, dt=now)
    shift_date = shift_utils.shift_date_for_datetime(shift, now)
    result = calculate_shift_summary(
        db,
        line_id=line_id,
        shift_date=shift_date,
        shift_code=shift_code,
        as_of=now,
        require_complete=False,
    )
    eta = _calc_eta(
        result["total_produced"],
        result["target"],
        result["avg_cycle_time"],
    )

    return {
        "shift": shift_code,
        "shift_date": str(shift_date),
        "target": result["target"],
        "produced": result["total_produced"],
        "scrap": result["total_scrap"],
        "good": result["total_good"],
        "model_id": result["model_id"],
        "avg_cycle_time": result["avg_cycle_time"],
        "first_cycle_at": result["first_cycle_at"],
        "last_cycle_at": result["last_cycle_at"],
        "total_time_min": round(result["planned_base_sec"] / 60),
        "planned_min": round(result["planned_production_sec"] / 60),
        "downtime_min": round(result["total_downtime_sec"] / 60),
        "break_min": round(result["break_sec"] / 60),
        "available_min": round(result["run_time_sec"] / 60),
        "ideal_cycle_sec": result["ideal_cycle_sec"],
        "ideal_cycle_detail": result["ideal_cycle_detail"],
        "theoretical_output": result["theoretical_output"],
        "formula_version": result["formula_version"],
        "oee": {
            "availability": result["oee_availability"],
            "performance": result["oee_performance"],
            "quality": result["oee_quality"],
            "overall": result["oee_overall"],
        },
        "eta": eta,
    }


def _stored_oee_component(row: dict):
    """Kayıtlı vardiyayı süre ağırlıklı toplama için normalize eder."""
    import shift_utils

    planned_base = row.get("planned_base_sec")
    if planned_base is None:
        planned_base = shift_utils.planned_seconds(
            row["shift"],
            line_id=row["line_id"],
            dt=row["shift_date"],
        )
    planned_base = max(int(planned_base or 0), 0)
    excluded = min(int(row.get("break_sec") or 0), planned_base)

    planned = row.get("planned_production_sec")
    if planned is None:
        planned = max(planned_base - excluded, 0)
    planned = min(max(int(planned or 0), 0), planned_base)

    run_time = row.get("run_time_sec")
    if run_time is None:
        downtime = min(int(row.get("total_downtime_sec") or 0), planned)
        run_time = max(planned - downtime, 0)
    run_time = min(max(int(run_time or 0), 0), planned)

    ideal_time = row.get("ideal_time_sec")
    if ideal_time is None:
        performance = float(row.get("oee_performance") or 0)
        ideal_time = run_time * performance / 100.0
    normalized = dict(row)
    normalized.update({
        "planned_base_sec": planned_base,
        "planned_production_sec": planned,
        "run_time_sec": run_time,
        "ideal_time_sec": float(ideal_time or 0),
        "is_complete": True,
    })
    return normalized


def _serialize_oee_row(row: dict):
    out = dict(row)
    out["shift_date"] = str(out["shift_date"])
    for key in (
        "first_cycle_at", "last_cycle_at", "calculated_at", "created_at",
    ):
        value = out.get(key)
        if value is not None and hasattr(value, "isoformat"):
            out[key] = value.isoformat()
    return out


@router.get("/oee")
def get_oee_analysis(
    line_id: int = Query(1, ge=1),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    shift: Optional[str] = Query(None),
    include_live: bool = Query(True),
    db: Session = Depends(get_db),
):
    """Tarih aralığı için süre/adet ağırlıklı OEE bileşenlerini döndürür."""
    import shift_utils

    today = date.today()
    date_to = date_to or today
    date_from = date_from or (date_to - timedelta(days=14))
    if date_to < date_from:
        raise HTTPException(422, "date_to, date_from değerinden önce olamaz")
    if (date_to - date_from).days > 366:
        raise HTTPException(422, "OEE analiz aralığı en fazla 366 gün olabilir")

    params = {"lid": line_id, "date_from": date_from, "date_to": date_to}
    shift_clause = ""
    if shift:
        shift_clause = " AND shift=:shift"
        params["shift"] = shift
    rows = db.execute(text(f"""
        SELECT summary_id, line_id, shift_date, shift, model_id, target,
               total_produced, total_scrap, total_good, avg_cycle_time,
               total_downtime_sec, break_sec, oee_availability,
               oee_performance, oee_quality, oee_overall,
               first_cycle_at, last_cycle_at, formula_version,
               calculated_at, created_at, planned_base_sec,
               planned_production_sec, run_time_sec, ideal_cycle_sec,
               ideal_time_sec, ideal_cycle_detail
        FROM shift_summary
        WHERE line_id=:lid
          AND shift_date BETWEEN :date_from AND :date_to
          {shift_clause}
        ORDER BY shift_date, shift
    """), params).fetchall()
    components = [_stored_oee_component(dict(row._mapping)) for row in rows]

    # Bugünün vardiya özeti henüz yazılmamışsa salt okunur canlı sonucu ekle.
    if include_live and date_from <= today <= date_to:
        now = datetime.now().astimezone()
        current_code = shift_utils.detect_shift_code(now, line_id=line_id)
        current_shift = shift_utils.shift_by_code(
            current_code,
            line_id=line_id,
            dt=now,
        )
        current_date = shift_utils.shift_date_for_datetime(current_shift, now)
        exists = any(
            row["shift_date"] == current_date and row["shift"] == current_code
            for row in components
        )
        if not exists and (not shift or shift == current_code):
            live = calculate_shift_summary(
                db,
                line_id=line_id,
                shift_date=current_date,
                shift_code=current_code,
                as_of=now,
                require_complete=False,
            )
            components.append(live)

    components.sort(key=lambda row: (row["shift_date"], row["shift"]))
    aggregate = aggregate_oee(components)
    aggregate["total_downtime_sec"] = sum(
        int(row.get("total_downtime_sec") or 0) for row in components
    )
    aggregate["break_sec"] = sum(
        int(row.get("break_sec") or 0) for row in components
    )
    aggregate["planned_base_sec"] = sum(
        int(row.get("planned_base_sec") or 0) for row in components
    )

    daily = []
    for day in sorted({row["shift_date"] for row in components}):
        day_rows = [row for row in components if row["shift_date"] == day]
        item = aggregate_oee(day_rows)
        item["date"] = str(day)
        item["total_downtime_sec"] = sum(
            int(row.get("total_downtime_sec") or 0) for row in day_rows
        )
        item["break_sec"] = sum(int(row.get("break_sec") or 0) for row in day_rows)
        daily.append(item)

    target_row = db.execute(text("""
        SELECT value
        FROM system_settings
        WHERE key='oee_target' AND scope='global'
        LIMIT 1
    """)).fetchone()
    try:
        oee_target = float(target_row[0]) if target_row else 67.0
    except (TypeError, ValueError):
        oee_target = 67.0

    return {
        "formula_version": FORMULA_VERSION,
        "filters": {
            "line_id": line_id,
            "date_from": str(date_from),
            "date_to": str(date_to),
            "shift": shift,
        },
        "oee_target": oee_target,
        "oee": {
            key: aggregate[key]
            for key in ("availability", "performance", "quality", "overall")
        },
        "totals": {
            key: aggregate[key]
            for key in (
                "planned_base_sec", "planned_production_sec", "run_time_sec",
                "total_downtime_sec", "break_sec", "total_produced",
                "total_scrap", "total_good", "summary_count",
                "ideal_cycle_sec", "theoretical_output",
            )
        },
        "daily": daily,
        "shifts": [_serialize_oee_row(row) for row in components],
    }


@router.get("/shifts")
def get_shifts(
    line_id: int = Query(1),
    days: int = Query(7),
    db: Session = Depends(get_db),
):
    rows = db.execute(text("""
        SELECT * FROM shift_summary
        WHERE line_id = :lid AND shift_date >= CURRENT_DATE - :days
        ORDER BY shift_date DESC, shift
    """), {"lid": line_id, "days": days}).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/daily")
def get_daily(
    line_id: int = Query(1),
    target_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    try:
        d = date.fromisoformat(target_date) if target_date else date.today()
    except ValueError as exc:
        raise HTTPException(422, "target_date YYYY-MM-DD biçiminde olmalı") from exc

    rows = db.execute(text("""
        SELECT summary_id, line_id, shift_date, shift, target,
               total_produced, total_scrap, total_good,
               total_downtime_sec, break_sec, oee_performance,
               planned_base_sec, planned_production_sec, run_time_sec,
               ideal_cycle_sec, ideal_time_sec, ideal_cycle_detail
        FROM shift_summary
        WHERE line_id=:lid AND shift_date=:d
    """), {"lid": line_id, "d": d}).fetchall()
    components = [_stored_oee_component(dict(row._mapping)) for row in rows]
    aggregate = aggregate_oee(components)
    return {
        "tarih": str(d),
        "toplam_uretim": aggregate["total_produced"],
        "toplam_fire": aggregate["total_scrap"],
        "toplam_iyi": aggregate["total_good"],
        "hedef": max((int(row.get("target") or 0) for row in components), default=0),
        "ort_oee": aggregate["overall"] if components else 0.0,
    }
