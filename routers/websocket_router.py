import uuid
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime, timedelta

from models.database import get_db
from models.orm_models import SensorReading, BatchTransfer

router = APIRouter(prefix="/api/v1/batch", tags=["Toplu Transfer"])


@router.get("/transfers", response_model=list)
def list_transfers(limit: int = Query(20, ge=1, le=100),
                   db: Session = Depends(get_db)):
    transfers = (db.query(BatchTransfer)
                 .order_by(desc(BatchTransfer.transferred_at))
                 .limit(limit).all())
    return [
        {"batch_id": t.batch_id, "record_count": t.record_count,
         "start_time": t.start_time.isoformat() if t.start_time else None,
         "end_time": t.end_time.isoformat() if t.end_time else None,
         "transferred_at": t.transferred_at.isoformat() if t.transferred_at else None,
         "status": t.status, "error_message": t.error_message}
        for t in transfers
    ]


@router.post("/prepare")
def prepare_batch(hours: int = Query(24, ge=1, le=168),
                  db: Session = Depends(get_db)):
    """PostgreSQL'den MSSQL'e aktarılacak verileri hazırla."""
    since = datetime.utcnow() - timedelta(hours=hours)

    # Daha önce transfer edilmemiş okumalar
    already_transferred = (
        db.query(SensorReading.reading_id)
        .filter(SensorReading.batch_id != None)
        .subquery()
    )
    readings = (
        db.query(SensorReading)
        .filter(SensorReading.reading_time >= since,
                ~SensorReading.reading_id.in_(already_transferred))
        .order_by(SensorReading.reading_time)
        .all()
    )

    if not readings:
        return {"status": "empty", "message": "Aktarılacak yeni okuma yok"}

    batch_id = f"batch-{uuid.uuid4().hex[:12]}"
    for r in readings:
        r.batch_id = batch_id
    db.commit()

    return {
        "status": "prepared",
        "batch_id": batch_id,
        "record_count": len(readings),
        "time_range": {
            "start": readings[0].reading_time.isoformat(),
            "end": readings[-1].reading_time.isoformat(),
        },
    }


@router.post("/transfer/{batch_id}")
def execute_transfer(batch_id: str, db: Session = Depends(get_db)):
    """
    Hazırlanan batch'i MSSQL'e aktar.
    Not: MSSQL bağlantısı aktif olduğunda buraya eklenir.
    Şu an sadece batch kaydı oluşturur (placeholder).
    """
    readings = (db.query(SensorReading)
                .filter(SensorReading.batch_id == batch_id).all())
    if not readings:
        raise HTTPException(404, f"Batch bulunamadı: {batch_id}")

    transfer = BatchTransfer(
        batch_id=batch_id,
        source_device="pi3b-postgresql",
        record_count=len(readings),
        start_time=readings[0].reading_time,
        end_time=readings[-1].reading_time,
        status="completed",          # MSSQL entegrasyonunda "pending" olacak
    )
    db.add(transfer)
    db.commit()

    return {
        "status": "completed",
        "batch_id": batch_id,
        "record_count": len(readings),
        "message": "MSSQL bağlantısı aktif olduğunda gerçek transfer başlayacak",
    }