"""
Sistem Sağlık Endpoint'i
routers/health.py
"""
import subprocess
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from models.database import get_db
from services.mqtt_service import mqtt_service

router = APIRouter(prefix="/api/v1/health", tags=["Sistem Sağlığı"])


def _systemctl_status(unit: str) -> dict:
    """systemd servisinin durumunu döner"""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=3
        )
        is_active = result.stdout.strip() == "active"
        # Servisin ne kadar süredir çalıştığını bul
        uptime = subprocess.run(
            ["systemctl", "show", unit, "--property=ActiveEnterTimestamp"],
            capture_output=True, text=True, timeout=3
        ).stdout.strip().replace("ActiveEnterTimestamp=", "")
        return {
            "unit": unit,
            "active": is_active,
            "since": uptime if uptime else None,
        }
    except Exception as e:
        return {"unit": unit, "active": False, "error": str(e)}


@router.get("/services")
def services_status():
    """Tüm kritik servislerin durumunu döner"""
    units = [
        "cerkezkoy_api",
        "production_logger",
        "downtime_monitor",
        "mosquitto",
        "postgresql",
    ]
    return {"services": [_systemctl_status(u) for u in units]}


@router.get("/data-flow")
def data_flow_status(db: Session = Depends(get_db)):
    """Veri akışının canlı olup olmadığını kontrol eder"""
    now = datetime.now()
    checks = {}

    # 1. MQTT — son 60sn içinde mesaj geldi mi?
    latest_mqtt = mqtt_service.get_latest()
    mqtt_topics = len(latest_mqtt) if latest_mqtt else 0
    checks["mqtt"] = {
        "topics_count": mqtt_topics,
        "healthy": mqtt_topics > 0,
    }

    # 2. PostgreSQL — son üretim güncellemesi ne zaman?
    try:
        row = db.execute(text("""
            SELECT updated_at FROM production_current WHERE line_id = 1
        """)).fetchone()
        if row and row[0]:
            last_update = row[0]
            if last_update.tzinfo is None:
                last_update = last_update.replace(tzinfo=now.astimezone().tzinfo)
            age_sec = (now.astimezone() - last_update.astimezone()).total_seconds()
            checks["production_data"] = {
                "last_update": str(last_update),
                "age_sec": round(age_sec),
                "healthy": age_sec < 60,  # 60sn'den eskiyse problem
            }
        else:
            checks["production_data"] = {"healthy": False, "error": "Veri yok"}
    except Exception as e:
        checks["production_data"] = {"healthy": False, "error": str(e)}

    # 3. Disk doluluk
    try:
        df = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=3)
        lines = df.stdout.strip().split("\n")
        if len(lines) >= 2:
            parts = lines[1].split()
            checks["disk"] = {
                "used": parts[2],
                "available": parts[3],
                "percent": parts[4],
                "healthy": int(parts[4].rstrip("%")) < 80,
            }
    except Exception as e:
        checks["disk"] = {"healthy": False, "error": str(e)}

    # 4. Genel skor
    all_healthy = all(c.get("healthy", False) for c in checks.values())
    return {
        "overall_healthy": all_healthy,
        "checked_at": str(now),
        "checks": checks,
    }