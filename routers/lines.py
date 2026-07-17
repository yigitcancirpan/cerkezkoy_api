from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from models.database import get_db

router = APIRouter(prefix="/api/v1/lines", tags=["Hatlar"])

@router.get("")
def list_lines(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT l.line_id, l.line_name, l.mqtt_prefix, s.site_code, s.site_name
        FROM production_lines l
        JOIN sites s ON s.site_id = l.site_id
        WHERE l.is_active = TRUE
        ORDER BY l.line_id
    """)).fetchall()
    return [dict(r._mapping) for r in rows]