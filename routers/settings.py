"""
Sistem Ayarları API — routers/settings.py
main.py'a ekle:  from routers import settings; app.include_router(settings.router)
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
from models.database import get_db

router = APIRouter(prefix="/api/v1/settings", tags=["Ayarlar"])

def _publish_config_reload():
    """Vardiya/ayar değişti → servislere anlık haber ver (cache invalidate)."""
    try:
        import paho.mqtt.publish as publish
        import json, time
        publish.single(
            "fabrika/config",   # hat bağımsız — tüm servisler dinler
            payload=json.dumps({"reload": True, "ts": time.time()}),
            hostname="127.0.0.1", port=1883,
        )
    except Exception:
        pass   # MQTT yoksa sessiz geç — 60sn TTL zaten devreye girer
    # API kendi shift_utils cache'ini de hemen tazele
    try:
        import shift_utils
        shift_utils.invalidate()
    except Exception:
        pass

class SettingValue(BaseModel):
    value: str


class OilThreshold(BaseModel):
    level_min: Optional[float] = None
    level_max: Optional[float] = None
    temp_min: Optional[float] = None
    temp_max: Optional[float] = None


@router.get("")
def get_settings(db: Session = Depends(get_db)):
    rows = db.execute(text("SELECT key, value FROM system_settings")).fetchall()
    return {r[0]: r[1] for r in rows}


@router.put("/{key}")
def set_setting(key: str, body: SettingValue, scope: str = "global",
                db: Session = Depends(get_db)):
    db.execute(text("""
        INSERT INTO system_settings (key, value, scope, updated_at)
        VALUES (:k, :v, :s, NOW())
        ON CONFLICT (key, scope) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
    """), {"k": key, "v": body.value, "s": scope})
    db.commit()
    return {"key": key, "value": body.value}


@router.get("/oil-thresholds")
def get_oil_thresholds(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT press_id, level_min, level_max, temp_min, temp_max
        FROM press_oil_thresholds ORDER BY press_id
    """)).fetchall()
    return [dict(r._mapping) for r in rows]


@router.put("/oil-thresholds/{press_id}")
def set_oil_threshold(press_id: int, body: OilThreshold, db: Session = Depends(get_db)):
    db.execute(text("""
        INSERT INTO press_oil_thresholds (press_id, level_min, level_max, temp_min, temp_max, updated_at)
        VALUES (:p, :lmin, :lmax, :tmin, :tmax, NOW())
        ON CONFLICT (press_id) DO UPDATE SET
            level_min = COALESCE(EXCLUDED.level_min, press_oil_thresholds.level_min),
            level_max = COALESCE(EXCLUDED.level_max, press_oil_thresholds.level_max),
            temp_min  = COALESCE(EXCLUDED.temp_min,  press_oil_thresholds.temp_min),
            temp_max  = COALESCE(EXCLUDED.temp_max,  press_oil_thresholds.temp_max),
            updated_at = NOW()
    """), {"p": press_id, "lmin": body.level_min, "lmax": body.level_max,
           "tmin": body.temp_min, "tmax": body.temp_max})
    db.commit()
    return {"press_id": press_id}

class ShiftUpsert(BaseModel):
    code: str
    label: str
    start_hour: int
    end_hour: int
    latest_end: int
    window_hours: int
    planned_seconds: int
    display_order: int = 0
    is_active: bool = True


@router.get("/shifts")
def get_shifts(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT code, label, start_hour, end_hour, latest_end,
               window_hours, planned_seconds, display_order, is_active
        FROM shift_config ORDER BY display_order, start_hour
    """)).fetchall()
    return [dict(r._mapping) for r in rows]


@router.put("/shifts/{code}")
def upsert_shift(code: str, body: ShiftUpsert, db: Session = Depends(get_db)):
    db.execute(text("""
        INSERT INTO shift_config
            (code, label, start_hour, end_hour, latest_end,
             window_hours, planned_seconds, display_order, is_active, line_id, updated_at)
        VALUES (:code,:label,:sh,:eh,:le,:wh,:ps,:ord,:act,:lid,NOW())
        ON CONFLICT (code, COALESCE(line_id, -1)) DO UPDATE SET
            label=EXCLUDED.label, start_hour=EXCLUDED.start_hour,
            end_hour=EXCLUDED.end_hour, latest_end=EXCLUDED.latest_end,
            window_hours=EXCLUDED.window_hours, planned_seconds=EXCLUDED.planned_seconds,
            display_order=EXCLUDED.display_order, is_active=EXCLUDED.is_active,
            updated_at=NOW()
    """), {"code": body.code, "label": body.label, "sh": body.start_hour,
           "eh": body.end_hour, "le": body.latest_end, "wh": body.window_hours,
           "ps": body.planned_seconds, "ord": body.display_order, "act": body.is_active,
           "lid": None})          # ← global vardiya: line_id = NULL
    db.commit()
    _publish_config_reload()
    return {"code": code, "message": "Vardiya kaydedildi"}


@router.delete("/shifts/{code}")
def delete_shift(code: str, db: Session = Depends(get_db)):
    res = db.execute(text("DELETE FROM shift_config WHERE code=:c RETURNING code"), {"c": code}).fetchone()
    db.commit()
    if not res:
        raise HTTPException(404, "Vardiya bulunamadı")
    _publish_config_reload()
    return {"deleted": code}