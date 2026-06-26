from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime, timedelta
from typing import Optional

from models.database import get_db
from models.orm_models import Alert, Sensor
from models.schemas import AlertCreate, AlertResponse, AlertAcknowledge, AlertType

router = APIRouter(prefix="/api/v1/alerts", tags=["Alarmlar"])


@router.get("/", response_model=list[AlertResponse])
def list_alerts(is_acknowledged: Optional[bool] = None,
                alert_type: Optional[AlertType] = None,
                machine_id: Optional[int] = None,
                limit: int = Query(50, ge=1, le=500),
                db: Session = Depends(get_db)):
    q = db.query(Alert)
    if is_acknowledged is not None:
        q = q.filter(Alert.is_acknowledged == is_acknowledged)
    if alert_type:
        q = q.filter(Alert.alert_type == alert_type.value)
    if machine_id:
        q = q.filter(Alert.machine_id == machine_id)
    return q.order_by(desc(Alert.triggered_at)).limit(limit).all()


@router.get("/active", response_model=list[AlertResponse])
def get_active_alerts(db: Session = Depends(get_db)):
    return (db.query(Alert)
            .filter(Alert.is_acknowledged == False, Alert.resolved_at == None)
            .order_by(desc(Alert.triggered_at)).all())


@router.get("/summary")
def alert_summary(hours: int = Query(24, ge=1, le=720),
                  db: Session = Depends(get_db)):
    since = datetime.utcnow() - timedelta(hours=hours)
    alerts = db.query(Alert).filter(Alert.triggered_at >= since).all()
    total = len(alerts)
    by_type = {}
    for a in alerts:
        by_type[a.alert_type] = by_type.get(a.alert_type, 0) + 1
    unacked = sum(1 for a in alerts if not a.is_acknowledged)
    return {"period_hours": hours, "total": total, "unacknowledged": unacked,
            "by_type": by_type}


@router.post("/", response_model=AlertResponse, status_code=201)
def create_alert(alert: AlertCreate, db: Session = Depends(get_db)):
    sensor = db.query(Sensor).filter(Sensor.sensor_id == alert.sensor_id).first()
    if not sensor:
        raise HTTPException(404, "Sensör bulunamadı")
    db_alert = Alert(**alert.model_dump())
    db.add(db_alert)
    db.commit()
    db.refresh(db_alert)
    return db_alert


@router.patch("/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int, body: AlertAcknowledge,
                      db: Session = Depends(get_db)):
    alert = db.query(Alert).filter(Alert.alert_id == alert_id).first()
    if not alert:
        raise HTTPException(404, "Alarm bulunamadı")
    alert.is_acknowledged = True
    alert.acknowledged_by = body.acknowledged_by
    alert.acknowledged_at = datetime.utcnow()
    db.commit()
    return {"status": "ok", "alert_id": alert_id, "acknowledged_by": body.acknowledged_by}


@router.patch("/{alert_id}/resolve")
def resolve_alert(alert_id: int, db: Session = Depends(get_db)):
    alert = db.query(Alert).filter(Alert.alert_id == alert_id).first()
    if not alert:
        raise HTTPException(404, "Alarm bulunamadı")
    alert.resolved_at = datetime.utcnow()
    if not alert.is_acknowledged:
        alert.is_acknowledged = True
        alert.acknowledged_at = datetime.utcnow()
        alert.acknowledged_by = "auto-resolve"
    db.commit()
    return {"status": "ok", "alert_id": alert_id, "resolved_at": alert.resolved_at.isoformat()}