from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from datetime import datetime, timedelta
from typing import Optional

from models.database import get_db
from models.orm_models import SensorReading, Sensor, Machine, ProductionLine
from models.schemas import (
    SensorReadingCreate, SensorReadingBulk, SensorReadingResponse,
    SensorStatsResponse, SensorType
)

router = APIRouter(prefix="/api/v1/sensors", tags=["Sensörler"])


def parse_time_range(time_range: str) -> datetime:
    mapping = {"1h": timedelta(hours=1), "6h": timedelta(hours=6),
               "12h": timedelta(hours=12), "24h": timedelta(hours=24),
               "7d": timedelta(days=7), "30d": timedelta(days=30)}
    delta = mapping.get(time_range)
    if not delta:
        raise HTTPException(400, f"Geçersiz zaman aralığı: {time_range}")
    return datetime.utcnow() - delta


@router.get("/latest")
def get_latest_readings(location: Optional[str] = None,
                        sensor_type: Optional[str] = None,
                        db: Session = Depends(get_db)):
    subquery = (
        db.query(SensorReading.sensor_id,
                 func.max(SensorReading.reading_time).label("max_time"))
        .filter(SensorReading.reading_time > datetime.utcnow() - timedelta(minutes=5))
    )
    if location:
        subquery = subquery.join(ProductionLine, SensorReading.line_id == ProductionLine.line_id
                                 ).filter(ProductionLine.location == location)
    if sensor_type:
        subquery = subquery.filter(SensorReading.sensor_type == sensor_type)
    subquery = subquery.group_by(SensorReading.sensor_id).subquery()

    readings = (db.query(SensorReading)
                .join(subquery, ((SensorReading.sensor_id == subquery.c.sensor_id) &
                                 (SensorReading.reading_time == subquery.c.max_time)))
                .all())

    return {"count": len(readings), "data": [
        {"sensor_id": r.sensor_id, "machine_id": r.machine_id,
         "sensor_type": r.sensor_type, "value": r.value,
         "quality": r.quality, "reading_time": r.reading_time.isoformat()}
        for r in readings
    ]}


@router.get("/history/{sensor_id}")
def get_sensor_history(sensor_id: int,
                       time_range: str = Query("1h", description="1h, 6h, 24h, 7d, 30d"),
                       db: Session = Depends(get_db)):
    since = parse_time_range(time_range)
    readings = (db.query(SensorReading.value, SensorReading.quality, SensorReading.reading_time)
                .filter(SensorReading.sensor_id == sensor_id, SensorReading.reading_time >= since)
                .order_by(desc(SensorReading.reading_time)).limit(5000).all())
    return {"sensor_id": sensor_id, "time_range": time_range, "count": len(readings),
            "data": [{"value": r.value, "quality": r.quality, "time": r.reading_time.isoformat()}
                     for r in readings]}


@router.get("/stats/{sensor_id}")
def get_sensor_stats(sensor_id: int, time_range: str = Query("24h"),
                     db: Session = Depends(get_db)):
    since = parse_time_range(time_range)
    stats = (db.query(func.avg(SensorReading.value).label("avg_value"),
                      func.max(SensorReading.value).label("max_value"),
                      func.min(SensorReading.value).label("min_value"),
                      func.stddev(SensorReading.value).label("stddev_value"),
                      func.count(SensorReading.reading_id).label("reading_count"))
             .filter(SensorReading.sensor_id == sensor_id, SensorReading.reading_time >= since)
             .first())
    sensor = db.query(Sensor).filter(Sensor.sensor_id == sensor_id).first()
    if not sensor:
        raise HTTPException(404, "Sensör bulunamadı")
    machine = db.query(Machine).filter(Machine.machine_id == sensor.machine_id).first()
    line = db.query(ProductionLine).filter(ProductionLine.line_id == machine.line_id).first()
    return SensorStatsResponse(
        sensor_id=sensor_id, sensor_type=sensor.sensor_type,
        location=line.location if line else "",
        machine_name=machine.machine_name if machine else "",
        avg_value=round(stats.avg_value or 0, 2), max_value=round(stats.max_value or 0, 2),
        min_value=round(stats.min_value or 0, 2),
        stddev_value=round(stats.stddev_value or 0, 3) if stats.stddev_value else None,
        reading_count=stats.reading_count or 0, period=time_range)


@router.post("/reading")
def post_reading(reading: SensorReadingCreate, db: Session = Depends(get_db)):
    db_reading = SensorReading(
        sensor_id=reading.sensor_id, machine_id=reading.machine_id,
        line_id=reading.line_id, sensor_type=reading.sensor_type.value,
        value=reading.value, quality=reading.quality, device_id=reading.device_id)
    db.add(db_reading)
    db.commit()
    return {"status": "ok", "reading_id": db_reading.reading_id}


@router.post("/readings/bulk")
def post_bulk_readings(bulk: SensorReadingBulk, db: Session = Depends(get_db)):
    db_readings = [
        SensorReading(sensor_id=r.sensor_id, machine_id=r.machine_id, line_id=r.line_id,
                      sensor_type=r.sensor_type.value, value=r.value,
                      quality=r.quality, device_id=r.device_id)
        for r in bulk.readings
    ]
    db.add_all(db_readings)
    db.commit()
    return {"status": "ok", "count": len(db_readings)}