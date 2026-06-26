"""
Fire Takip API
routers/scrap.py

Endpoint'ler:
  GET    /api/v1/scrap/reasons            → Fire sebepleri (ayrı liste)
  POST   /api/v1/scrap/entry              → Fire kaydı (adet + sebep)
  GET    /api/v1/scrap/summary/{line_id}  → Bir günün/vardiyanın fire özeti
  GET    /api/v1/scrap/history            → Fire kayıtları (düzeltme/denetim)
  DELETE /api/v1/scrap/entry/{id}         → Yanlış kaydı sil
  GET    /api/v1/scrap/analysis           → Sebep bazlı + günlük (analiz sayfası için)

main.py'a ekle:
    from routers import scrap
    app.include_router(scrap.router)
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import date, datetime, timedelta
from typing import Optional

from models.database import get_db

router = APIRouter(prefix="/api/v1/scrap", tags=["Fire Takip"])


class ScrapEntryRequest(BaseModel):
    line_id: int
    reason_id: int
    qty: int
    shift: Optional[str] = None
    operator_name: Optional[str] = None
    notes: Optional[str] = None
    source: Optional[str] = "terminal"


# ─── 1) FİRE SEBEPLERİ ───────────────────────
@router.get("/reasons")
def get_scrap_reasons(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT reason_id, reason_code, reason_name, category, color_hex, icon, display_order
        FROM scrap_reasons
        WHERE is_active = TRUE
        ORDER BY display_order, reason_id
    """)).fetchall()
    return [dict(r._mapping) for r in rows]


# ─── 2) FİRE KAYDI ───────────────────────────
@router.post("/entry", status_code=201)
def create_scrap(req: ScrapEntryRequest, db: Session = Depends(get_db)):
    if req.qty <= 0:
        raise HTTPException(400, "Adet 0'dan büyük olmalı")

    rr = db.execute(
        text("SELECT reason_name FROM scrap_reasons WHERE reason_id = :rid AND is_active = TRUE"),
        {"rid": req.reason_id},
    ).fetchone()
    if not rr:
        raise HTTPException(404, f"Fire sebebi bulunamadı: reason_id={req.reason_id}")

    row = db.execute(text("""
        INSERT INTO scrap_entries
            (line_id, reason_id, qty, shift, operator_name, notes, source, created_at)
        VALUES (:lid, :rid, :qty, :shift, :op, :notes, :src, NOW())
        RETURNING id, created_at
    """), {
        "lid": req.line_id, "rid": req.reason_id, "qty": req.qty,
        "shift": req.shift, "op": req.operator_name, "notes": req.notes,
        "src": req.source or "terminal",
    }).fetchone()
    db.commit()

    today_total = db.execute(text("""
        SELECT COALESCE(SUM(qty), 0)
        FROM scrap_entries
        WHERE line_id = :lid AND created_at::date = CURRENT_DATE
    """), {"lid": req.line_id}).fetchone()

    return {
        "id": row[0],
        "created_at": str(row[1]),
        "reason_name": rr[0],
        "qty": req.qty,
        "today_total": int(today_total[0]),
        "message": f"{req.qty} adet fire kaydedildi — {rr[0]}",
    }


# ─── 3) GÜN/VARDİYA ÖZETİ ────────────────────
@router.get("/summary/{line_id}")
def scrap_summary(
    line_id: int,
    target_date: Optional[str] = Query(None, description="YYYY-MM-DD — boşsa bugün"),
    shift: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    d = target_date or str(date.today())
    params = {"lid": line_id, "d": d}
    shift_clause = ""
    if shift:
        shift_clause = " AND e.shift = :shift"
        params["shift"] = shift

    rows = db.execute(text(f"""
        SELECT r.reason_code, r.reason_name, r.color_hex,
               COALESCE(SUM(e.qty), 0) AS qty
        FROM scrap_entries e
        JOIN scrap_reasons r ON e.reason_id = r.reason_id
        WHERE e.line_id = :lid AND e.created_at::date = :d {shift_clause}
        GROUP BY r.reason_code, r.reason_name, r.color_hex
        ORDER BY qty DESC
    """), params).fetchall()

    by_reason = [dict(x._mapping) for x in rows]
    total = sum(int(x["qty"]) for x in by_reason)
    return {"date": d, "shift": shift, "total_qty": total, "by_reason": by_reason}


# ─── 4) GEÇMİŞ (düzeltme/denetim) ────────────
@router.get("/history")
def scrap_history(
    line_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    clauses, params = [], {"lim": limit}
    if line_id:
        clauses.append("e.line_id = :lid"); params["lid"] = line_id
    if date_from:
        clauses.append("e.created_at >= :df"); params["df"] = date_from
    if date_to:
        clauses.append("e.created_at < :dt")
        params["dt"] = (datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = db.execute(text(f"""
        SELECT e.id, e.line_id, e.qty, e.shift, e.operator_name, e.notes, e.source,
               e.created_at, r.reason_code, r.reason_name, r.color_hex
        FROM scrap_entries e
        JOIN scrap_reasons r ON e.reason_id = r.reason_id
        {where}
        ORDER BY e.created_at DESC
        LIMIT :lim
    """), params).fetchall()
    return {"items": [dict(x._mapping) for x in rows]}


# ─── 5) KAYIT SİL (düzeltme) ─────────────────
@router.delete("/entry/{entry_id}")
def delete_scrap(entry_id: int, db: Session = Depends(get_db)):
    res = db.execute(text("DELETE FROM scrap_entries WHERE id = :id RETURNING id"), {"id": entry_id}).fetchone()
    db.commit()
    if not res:
        raise HTTPException(404, "Kayıt bulunamadı")
    return {"deleted": entry_id}


# ─── 6) ANALİZ (sebep bazlı + günlük) ────────
@router.get("/analysis")
def scrap_analysis(
    line_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    shift: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    clauses, params = ["e.qty > 0"], {}
    if line_id:
        clauses.append("e.line_id = :lid"); params["lid"] = line_id
    if shift:
        clauses.append("e.shift = :shift"); params["shift"] = shift
    if date_from:
        clauses.append("e.created_at >= :df"); params["df"] = date_from
    if date_to:
        clauses.append("e.created_at < :dt")
        params["dt"] = (datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    where = "WHERE " + " AND ".join(clauses)

    reason_rows = db.execute(text(f"""
        SELECT r.reason_code, r.reason_name, r.category, r.color_hex,
               COUNT(e.id) AS cnt, COALESCE(SUM(e.qty), 0) AS qty
        FROM scrap_entries e
        JOIN scrap_reasons r ON e.reason_id = r.reason_id
        {where}
        GROUP BY r.reason_code, r.reason_name, r.category, r.color_hex
    """), params).fetchall()
    reasons = [dict(x._mapping) for x in reason_rows]
    for r in reasons:
        r["qty"] = int(r["qty"]); r["cnt"] = int(r["cnt"])
    reasons.sort(key=lambda x: x["qty"], reverse=True)

    daily_rows = db.execute(text(f"""
        SELECT e.created_at::date AS d, r.reason_code, COALESCE(SUM(e.qty), 0) AS qty
        FROM scrap_entries e
        JOIN scrap_reasons r ON e.reason_id = r.reason_id
        {where}
        GROUP BY e.created_at::date, r.reason_code
    """), params).fetchall()
    daily_map = {}
    for d, code, qty in daily_rows:
        daily_map.setdefault(str(d), {})[code] = int(qty)
    daily = [{"date": ds, "reasons": daily_map[ds], "total_qty": sum(daily_map[ds].values())}
             for ds in sorted(daily_map.keys())]

    return {
        "total_qty": sum(r["qty"] for r in reasons),
        "reasons": reasons,
        "daily": daily,
        "reason_meta": {r["reason_code"]: {"name": r["reason_name"], "color": r["color_hex"], "category": r["category"]} for r in reasons},
    }
class ScrapReasonUpdate(BaseModel):
    reason_name: Optional[str] = None
    category: Optional[str] = None
    color_hex: Optional[str] = None
    icon: Optional[str] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None


class ScrapReasonCreate(BaseModel):
    reason_code: str
    reason_name: str
    category: str = "quality"
    color_hex: str = "#ef4444"
    icon: str = ""
    display_order: int = 0


@router.get("/reasons/manage")
def scrap_reasons_manage(db: Session = Depends(get_db)):
    rows = db.execute(text("""
        SELECT reason_id, reason_code, reason_name, category, color_hex, icon,
               display_order, is_active
        FROM scrap_reasons ORDER BY display_order, reason_id
    """)).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/reasons", status_code=201)
def create_scrap_reason(req: ScrapReasonCreate, db: Session = Depends(get_db)):
    code = req.reason_code.strip().upper()
    if not code:
        raise HTTPException(400, "Kod boş olamaz")
    if db.execute(text("SELECT 1 FROM scrap_reasons WHERE reason_code=:c"), {"c": code}).fetchone():
        raise HTTPException(409, f"Bu kod zaten var: {code}")
    row = db.execute(text("""
        INSERT INTO scrap_reasons (reason_code, reason_name, category, color_hex, icon, display_order, is_active)
        VALUES (:c,:n,:cat,:col,:ic,:ord,TRUE) RETURNING reason_id
    """), {"c": code, "n": req.reason_name, "cat": req.category,
           "col": req.color_hex, "ic": req.icon, "ord": req.display_order}).fetchone()
    db.commit()
    return {"reason_id": row[0], "message": "Fire sebebi eklendi"}


@router.patch("/reasons/{reason_id}")
def update_scrap_reason(reason_id: int, req: ScrapReasonUpdate, db: Session = Depends(get_db)):
    fields = {k: v for k, v in req.dict().items() if v is not None}
    if not fields:
        raise HTTPException(400, "Güncellenecek alan yok")
    sets = ", ".join(f"{k} = :{k}" for k in fields)   # anahtarlar sabit alan adları — enjeksiyon yok
    fields["rid"] = reason_id
    res = db.execute(
        text(f"UPDATE scrap_reasons SET {sets} WHERE reason_id = :rid RETURNING reason_id"),
        fields
    ).fetchone()
    db.commit()
    if not res:
        raise HTTPException(404, "Sebep bulunamadı")
    return {"reason_id": res[0], "message": "Güncellendi"}