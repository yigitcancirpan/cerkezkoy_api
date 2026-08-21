"""
Sistem Ayarları API — routers/settings.py
main.py'a ekle:  from routers import settings; app.include_router(settings.router)
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
from models.database import get_db

router = APIRouter(prefix="/api/v1/settings", tags=["Ayarlar"])

def _publish_config_reload():
    """Vardiya/ayar değişti → servislere anlık haber ver (cache invalidate)."""
    try:
        import paho.mqtt.publish as publish
        import json, os, time
        username = os.getenv("MQTT_USERNAME")
        auth = None
        if username:
            auth = {
                "username": username,
                "password": os.getenv("MQTT_PASSWORD", ""),
            }
        publish.single(
            "fabrika/config",   # hat bağımsız — tüm servisler dinler
            payload=json.dumps({"reload": True, "ts": time.time()}),
            hostname=os.getenv("MQTT_BROKER", "127.0.0.1"),
            port=int(os.getenv("MQTT_PORT", "1883")),
            auth=auth,
        )
    except Exception as exc:
        # MQTT geçici yoksa 60sn TTL yine devreye girer; hata görünür olsun.
        import logging
        logging.getLogger("settings").warning(
            "Vardiya config MQTT bildirimi gönderilemedi: %s",
            exc,
        )
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
    start_hour: int = Field(ge=0, le=23)
    end_hour: int = Field(ge=1, le=24)
    latest_end: int = Field(ge=0, le=24)
    window_hours: int = Field(ge=1, le=24)
    planned_seconds: int = Field(gt=0)
    display_order: int = 0
    is_active: bool = True
    line_id: Optional[int] = None
    # PostgreSQL convention: 0=Pazar, 1=Pazartesi, ... 6=Cumartesi.
    # NULL olan satır genel varsayılandır.
    day_of_week: Optional[int] = Field(default=None, ge=0, le=6)


def _shift_key_params(code: str, line_id: int, day_of_week: int):
    return {
        "code": code,
        "lid": None if line_id == -1 else line_id,
        "dow": None if day_of_week == -1 else day_of_week,
    }


def _shift_exists(db: Session, code: str, line_id, day_of_week) -> bool:
    return db.execute(text("""
        SELECT 1
        FROM shift_config
        WHERE code = :code
          AND line_id IS NOT DISTINCT FROM :lid
          AND day_of_week IS NOT DISTINCT FROM :dow
        LIMIT 1
    """), {"code": code, "lid": line_id, "dow": day_of_week}).fetchone() is not None


def _shift_values(body: ShiftUpsert):
    end_offset = body.end_hour if body.end_hour > body.start_hour else body.end_hour + 24
    latest_offset = (
        body.latest_end
        if body.latest_end > body.start_hour
        else body.latest_end + 24
    )
    if latest_offset < end_offset:
        raise HTTPException(
            422,
            "Özet yazma saati vardiya bitişinden önce olamaz",
        )
    calculated_window = end_offset - body.start_hour
    return {
        "code": body.code,
        "label": body.label,
        "sh": body.start_hour,
        "eh": body.end_hour,
        "le": body.latest_end,
        "wh": calculated_window,
        "ps": body.planned_seconds,
        "ord": body.display_order,
        "act": body.is_active,
        "lid": body.line_id,
        "dow": body.day_of_week,
    }


@router.get("/shifts")
def get_shifts(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT code, label, start_hour, end_hour, latest_end,
               window_hours, planned_seconds, display_order, is_active,
               line_id, day_of_week
        FROM shift_config
        ORDER BY day_of_week NULLS FIRST, line_id NULLS FIRST,
                 display_order, start_hour
    """)).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/shifts", status_code=201)
def create_shift(body: ShiftUpsert, db: Session = Depends(get_db)):
    values = _shift_values(body)
    if _shift_exists(db, body.code, body.line_id, body.day_of_week):
        raise HTTPException(
            409,
            "Bu kod, hat ve gün kapsamı için vardiya zaten var",
        )
    db.execute(text("""
        INSERT INTO shift_config
            (code, label, start_hour, end_hour, latest_end,
             window_hours, planned_seconds, display_order, is_active,
             line_id, day_of_week, updated_at)
        VALUES (:code,:label,:sh,:eh,:le,:wh,:ps,:ord,:act,:lid,:dow,NOW())
    """), values)
    db.commit()
    _publish_config_reload()
    return {"code": body.code, "message": "Vardiya oluşturuldu"}


@router.put("/shifts/{code}")
def update_shift(
    code: str,
    body: ShiftUpsert,
    scope_line_id: int = Query(-1, ge=-1),
    scope_day_of_week: int = Query(-1, ge=-1, le=6),
    db: Session = Depends(get_db),
):
    original = _shift_key_params(code, scope_line_id, scope_day_of_week)
    if not _shift_exists(db, code, original["lid"], original["dow"]):
        raise HTTPException(404, "Düzenlenecek vardiya satırı bulunamadı")

    # Kapsam değiştiriliyorsa hedef anahtarın başka satıra ait olmadığını
    # önceden denetle. Böylece genel ve cumartesi vardiyaları karışmaz.
    target_changed = (
        body.code != code
        or body.line_id != original["lid"]
        or body.day_of_week != original["dow"]
    )
    if target_changed and _shift_exists(
        db,
        body.code,
        body.line_id,
        body.day_of_week,
    ):
        raise HTTPException(409, "Hedef vardiya kapsamı zaten kullanılıyor")

    values = _shift_values(body)
    values.update({"old_code": code, "old_lid": original["lid"], "old_dow": original["dow"]})
    result = db.execute(text("""
        UPDATE shift_config
        SET code=:code, label=:label, start_hour=:sh, end_hour=:eh,
            latest_end=:le, window_hours=:wh, planned_seconds=:ps,
            display_order=:ord, is_active=:act, line_id=:lid,
            day_of_week=:dow, updated_at=NOW()
        WHERE code=:old_code
          AND line_id IS NOT DISTINCT FROM :old_lid
          AND day_of_week IS NOT DISTINCT FROM :old_dow
    """), values)
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "Vardiya satırı tekil olarak güncellenemedi")
    db.commit()
    _publish_config_reload()
    return {"code": body.code, "message": "Vardiya güncellendi"}


@router.delete("/shifts/{code}")
def delete_shift(
    code: str,
    scope_line_id: int = Query(-1, ge=-1),
    scope_day_of_week: int = Query(-1, ge=-1, le=6),
    db: Session = Depends(get_db),
):
    params = _shift_key_params(code, scope_line_id, scope_day_of_week)
    res = db.execute(text("""
        DELETE FROM shift_config
        WHERE code=:code
          AND line_id IS NOT DISTINCT FROM :lid
          AND day_of_week IS NOT DISTINCT FROM :dow
        RETURNING code
    """), params).fetchone()
    db.commit()
    if not res:
        raise HTTPException(404, "Vardiya bulunamadı")
    _publish_config_reload()
    return {"deleted": code}
