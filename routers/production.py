"""
Üretim Takip API Router — v2
routers/production.py

Yeni endpoint'ler:
  GET  /api/v1/production/current/{line_id}  → Anlık + ilk/son baskı + ETA
  POST /api/v1/production/summary/now        → Manuel vardiya özeti hesapla
  GET  /api/v1/production/shifts             → Vardiya özetleri
  GET  /api/v1/production/daily              → Günlük özet
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
from datetime import date, datetime, timedelta
import math

from models.database import get_db

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

    # ── İLK BASKI: snapshot DEĞİL, production_log'dan türet (restart-proof) ──
    # production_log append-only ve kalıcı; restart öncesi kayıtlar tabloda kalır,
    # bu yüzden MIN(logged_at) restart'tan etkilenmez. Vardiya özeti de aynı kaynağı
    # kullanıyor → dashboard ile geçmiş her zaman tutarlı.
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

    # Vardiya içi üretim
    shift_produced = (result.get("produced") or 0) - (result.get("shift_start_produced") or 0)
    result["shift_produced"] = max(shift_produced, 0)

    eta = _calc_eta(result.get("produced"), result.get("target"), result.get("average_cycle"))
    result["eta"] = eta

    return result


@router.post("/summary/now")
def calculate_summary_now(
    line_id: int = Query(1),
    db: Session = Depends(get_db),
):
    """Manuel vardiya özeti — şu ana kadar olan verilerle hesapla"""
    # Şu anki vardiyayı belirle
    h = datetime.now().hour
    if 8 <= h < 19:
        shift_code = "vardiya_1"
    elif 19 <= h or h < 3:
        shift_code = "vardiya_2"
    else:
        shift_code = "vardiya_1"

    today = date.today()

    # Üretim verileri — production_current'tan
    current = db.execute(text("""
        SELECT produced, scrap, target, model_id, first_cycle_at, last_cycle_at,
               actual_cycle, average_cycle
        FROM production_current WHERE line_id = :lid
    """), {"lid": line_id}).fetchone()

    if not current:
        return {"error": "Üretim verisi yok"}

    c = dict(current._mapping)
    produced = c.get("produced") or 0
    target = c.get("target") or 0
    avg_cycle = c.get("average_cycle") or 0

    # ── Gerçek fire: scrap_entries (bugün + vardiya) — PLC scrap değil ──
    scrap_row = db.execute(text("""
        SELECT COALESCE(SUM(qty), 0) AS s
        FROM scrap_entries
        WHERE line_id = :lid AND shift = :shift AND created_at::date = :d
    """), {"lid": line_id, "shift": shift_code, "d": today}).fetchone()
    scrap = int(scrap_row[0]) if scrap_row else 0
    good = max(produced - scrap, 0)

    # Duruş toplamı — VARDIYA_SONU hariç
    dt_row = db.execute(text("""
        SELECT COALESCE(SUM(d.duration_sec), 0) as total_dt
        FROM downtimes d
        JOIN downtime_reasons r ON d.reason_id = r.reason_id
        WHERE d.line_id = :lid AND d.started_at::date = :d AND d.is_active = FALSE
          AND r.reason_code <> 'VARDIYA_SONU'
    """), {"lid": line_id, "d": today}).fetchone()
    total_dt = dt_row[0] if dt_row else 0

    # Aktif duruş süresi de ekle — VARDIYA_SONU hariç
    active_dt = db.execute(text("""
        SELECT COALESCE(
            EXTRACT(EPOCH FROM (NOW() - d.started_at))::int, 0
        ) as active_sec
        FROM downtimes d
        JOIN downtime_reasons r ON d.reason_id = r.reason_id
        WHERE d.line_id = :lid AND d.is_active = TRUE
          AND r.reason_code <> 'VARDIYA_SONU'
    """), {"lid": line_id}).fetchone()
    if active_dt:
        total_dt += active_dt[0]

    # Çalışma süresi hesapla
    # ── Planlanan süre: vardiya başlangıcından ŞİMDİYE kadar ──
    now_dt = datetime.now()
    if shift_code == "vardiya_1":
        shift_start = now_dt.replace(hour=8, minute=0, second=0, microsecond=0)
        full_shift_sec = 10 * 3600
    else:  # vardiya_2 (18:00 → 24:00)
        shift_start = now_dt.replace(hour=18, minute=0, second=0, microsecond=0)
        if now_dt.hour < 18:
            shift_start -= timedelta(days=1)
        full_shift_sec = 6 * 3600

    elapsed = (now_dt - shift_start).total_seconds()
    total_time = max(min(elapsed, full_shift_sec), 60)   # vardiya başından önce/sonra sınırla

    available = max(total_time - total_dt, 1)

    availability = min(available / total_time * 100, 100.0) if total_time > 0 else 0
    ideal = avg_cycle / 10.0 if avg_cycle > 0 else 8.0
    performance = min((produced * ideal / available * 100) if available > 0 else 0, 100.0)
    quality = (good / produced * 100) if produced > 0 else 100
    oee = availability * performance * quality / 10000

    # ETA (cycle time bazlı)
    eta = _calc_eta(produced, target, avg_cycle)

    return {
        "shift": shift_code,
        "shift_date": str(today),
        "target": target,
        "produced": produced,
        "scrap": scrap,
        "good": good,
        "model_id": c.get("model_id"),
        "avg_cycle_time": avg_cycle,
        "first_cycle_at": str(c.get("first_cycle_at")) if c.get("first_cycle_at") else None,
        "last_cycle_at": str(c.get("last_cycle_at")) if c.get("last_cycle_at") else None,
        "total_time_min": round(total_time / 60),
        "downtime_min": round(total_dt / 60),
        "available_min": round(available / 60),
        "oee": {
            "availability": round(availability, 1),
            "performance": round(performance, 1),
            "quality": round(quality, 1),
            "overall": round(oee, 1),
        },
        "eta": eta,
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
    d = target_date or str(date.today())
    row = db.execute(text("""
        SELECT :d::date as tarih,
            COALESCE(SUM(total_produced),0) as toplam_uretim,
            COALESCE(SUM(total_scrap),0) as toplam_fire,
            COALESCE(SUM(total_good),0) as toplam_iyi,
            COALESCE(MAX(target),0) as hedef,
            COALESCE(AVG(oee_overall),0) as ort_oee
        FROM shift_summary WHERE line_id=:lid AND shift_date=:d::date
    """), {"lid": line_id, "d": d}).fetchone()
    return dict(row._mapping)


    