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
def set_setting(key: str, body: SettingValue, db: Session = Depends(get_db)):
    db.execute(text("""
        INSERT INTO system_settings (key, value, updated_at) VALUES (:k, :v, NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
    """), {"k": key, "v": body.value})
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