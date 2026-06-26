"""
Yağ Takip API
routers/oil.py

Endpoint'ler:
  GET /api/v1/oil/current        → 6 presin anlık değerleri
  GET /api/v1/oil/history        → Trend için zaman serisi
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import Optional
from datetime import datetime, timedelta

from models.database import get_db

router = APIRouter(prefix="/api/v1/oil", tags=["Yağ Takibi"])


@router.get("/current")
def get_current(db: Session = Depends(get_db)):
    """6 presin son okunan değerleri + 24h min/max"""
    rows = db.execute(text("""
        SELECT press_id, level, temperature,
               level_min_24h, level_max_24h,
               temp_min_24h, temp_max_24h,
               updated_at,
               EXTRACT(EPOCH FROM (NOW() - updated_at))::int as age_sec
        FROM press_oil_current
        ORDER BY press_id
    """)).fetchall()
    
    return {
        "presses": [dict(r._mapping) for r in rows],
        "fetched_at": datetime.now().isoformat(),
    }


@router.get("/history")
def get_history(
    press_id: Optional[int] = Query(None, description="Tek pres için filtrele"),
    hours: int = Query(24, ge=1, le=720, description="Kaç saatlik veri"),
    db: Session = Depends(get_db),
):
    """Trend grafiği için zaman serisi"""
    cutoff = datetime.now() - timedelta(hours=hours)
    
    where_clause = "WHERE recorded_at >= :cutoff"
    params = {"cutoff": cutoff}
    
    if press_id:
        where_clause += " AND press_id = :pid"
        params["pid"] = press_id
    
    rows = db.execute(text(f"""
        SELECT press_id, level, temperature, recorded_at
        FROM press_oil
        {where_clause}
        ORDER BY recorded_at ASC
    """), params).fetchall()
    
    # Press bazlı grupla
    by_press = {}
    for r in rows:
        d = dict(r._mapping)
        pid = d["press_id"]
        by_press.setdefault(pid, []).append({
            "t": d["recorded_at"].isoformat(),
            "level": d["level"],
            "temp": d["temperature"],
        })
    
    return {
        "hours": hours,
        "press_count": len(by_press),
        "data": by_press,
    }