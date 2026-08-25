"""
Fire Takip API
routers/scrap.py

Endpoint'ler:
  GET    /api/v1/scrap/reasons            → Fire sebepleri (ayrı liste)
  POST   /api/v1/scrap/entry              → Fire kaydı (adet + sebep)
  GET    /api/v1/scrap/entry-options      → Son 7 gün ve çözülmüş vardiyalar
  GET    /api/v1/scrap/summary/{line_id}  → Bir günün/vardiyanın fire özeti
  GET    /api/v1/scrap/history            → Fire kayıtları (düzeltme/denetim)
  DELETE /api/v1/scrap/entry/{id}         → Yanlış kaydı sil
  GET    /api/v1/scrap/analysis           → Sebep bazlı + günlük (analiz sayfası için)

main.py'a ekle:
    from routers import scrap
    app.include_router(scrap.router)
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import date, timedelta
from typing import Optional

from models.database import get_db
from services.scrap_service import (
    MAX_RETROACTIVE_DAYS,
    ScrapDateError,
    ScrapShiftError,
    historical_shift_options,
    recalculate_existing_summary,
    resolve_shift_code,
    summary_response,
    validate_production_date,
)

router = APIRouter(prefix="/api/v1/scrap", tags=["Fire Takip"])
logger = logging.getLogger("scrap")


class ScrapEntryRequest(BaseModel):
    line_id: int
    reason_id: int
    qty: int
    production_date: Optional[date] = None
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

    line = db.execute(
        text("""
            SELECT line_name
            FROM production_lines
            WHERE line_id=:line_id AND is_active=TRUE
        """),
        {"line_id": req.line_id},
    ).fetchone()
    if not line:
        raise HTTPException(404, f"Aktif hat bulunamadı: line_id={req.line_id}")

    rr = db.execute(
        text("""
            SELECT reason_name
            FROM scrap_reasons
            WHERE reason_id=:rid AND is_active=TRUE
        """),
        {"rid": req.reason_id},
    ).fetchone()
    if not rr:
        raise HTTPException(404, f"Fire sebebi bulunamadı: reason_id={req.reason_id}")

    try:
        production_date = validate_production_date(req.production_date)
        shift_code, _shifts = resolve_shift_code(
            line_id=req.line_id,
            production_date=production_date,
            requested_shift=req.shift,
        )
    except (ScrapDateError, ScrapShiftError) as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        row = db.execute(text("""
            INSERT INTO scrap_entries
                (line_id, reason_id, qty, production_date, shift,
                 operator_name, notes, source, created_at)
            VALUES
                (:lid, :rid, :qty, :production_date, :shift,
                 :op, :notes, :src, NOW())
            RETURNING id, created_at
        """), {
            "lid": req.line_id,
            "rid": req.reason_id,
            "qty": req.qty,
            "production_date": production_date,
            "shift": shift_code,
            "op": req.operator_name,
            "notes": req.notes,
            "src": req.source or "terminal",
        }).fetchone()

        recalculated = recalculate_existing_summary(
            db,
            line_id=req.line_id,
            production_date=production_date,
            shift=shift_code,
        )
        date_total = db.execute(text("""
            SELECT COALESCE(SUM(qty), 0)
            FROM scrap_entries
            WHERE line_id=:lid AND production_date=:production_date
        """), {
            "lid": req.line_id,
            "production_date": production_date,
        }).fetchone()
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception(
            "Fire kaydı/özet güncellemesi başarısız: line=%s date=%s shift=%s",
            req.line_id,
            production_date,
            shift_code,
        )
        raise HTTPException(
            409,
            "Fire kaydı ve vardiya özeti birlikte güncellenemedi; "
            "hiçbir değişiklik kaydedilmedi",
        ) from exc

    return {
        "id": row[0],
        "created_at": str(row[1]),
        "production_date": str(production_date),
        "shift": shift_code,
        "reason_name": rr[0],
        "qty": req.qty,
        "date_total": int(date_total[0]),
        "today_total": int(date_total[0]),  # eski istemcilerle uyumluluk
        "message": f"{req.qty} adet fire kaydedildi — {rr[0]}",
        **summary_response(recalculated),
    }


@router.get("/entry-options")
def scrap_entry_options(
    line_id: int,
    target_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    try:
        production_date = validate_production_date(target_date)
        _shift_code, shifts = resolve_shift_code(
            line_id=line_id,
            production_date=production_date,
            requested_shift=None,
        )
    except ScrapDateError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ScrapShiftError:
        import shift_utils

        shifts = shift_utils.get_shifts(line_id=line_id, dt=production_date)
        if not shifts:
            raise HTTPException(404, "Bu tarih ve hat için vardiya bulunamadı")

    shifts = historical_shift_options(
        db,
        line_id=line_id,
        production_date=production_date,
        resolved_shifts=shifts,
    )

    line = db.execute(text("""
        SELECT line_name
        FROM production_lines
        WHERE line_id=:line_id AND is_active=TRUE
    """), {"line_id": line_id}).fetchone()
    if not line:
        raise HTTPException(404, f"Aktif hat bulunamadı: line_id={line_id}")

    today = date.today()
    return {
        "line_id": line_id,
        "line_name": line[0],
        "target_date": str(production_date),
        "min_date": str(today - timedelta(days=MAX_RETROACTIVE_DAYS)),
        "max_date": str(today),
        "max_retroactive_days": MAX_RETROACTIVE_DAYS,
        "shifts": [
            {
                "code": item["code"],
                "label": item["label"],
                "start_hour": item["start_hour"],
                "end_hour": item["end_hour"],
            }
            for item in shifts
        ],
    }


# ─── 3) GÜN/VARDİYA ÖZETİ ────────────────────
@router.get("/summary/{line_id}")
def scrap_summary(
    line_id: int,
    target_date: Optional[date] = Query(None, description="YYYY-MM-DD — boşsa bugün"),
    shift: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    d = target_date or date.today()
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
        WHERE e.line_id = :lid AND e.production_date = :d {shift_clause}
        GROUP BY r.reason_code, r.reason_name, r.color_hex
        ORDER BY qty DESC
    """), params).fetchall()

    by_reason = [dict(x._mapping) for x in rows]
    total = sum(int(x["qty"]) for x in by_reason)
    return {"date": str(d), "shift": shift, "total_qty": total, "by_reason": by_reason}


# ─── 4) GEÇMİŞ (düzeltme/denetim) ────────────
@router.get("/history")
def scrap_history(
    line_id: Optional[int] = Query(None),
    date_from: Optional[date] = Query(None, description="YYYY-MM-DD"),
    date_to: Optional[date] = Query(None, description="YYYY-MM-DD"),
    shift: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    clauses, params = [], {"lim": limit}
    if line_id:
        clauses.append("e.line_id = :lid"); params["lid"] = line_id
    if date_from:
        clauses.append("e.production_date >= :df"); params["df"] = date_from
    if date_to:
        clauses.append("e.production_date <= :dt"); params["dt"] = date_to
    if shift:
        clauses.append("e.shift = :shift"); params["shift"] = shift
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = db.execute(text(f"""
        SELECT e.id, e.line_id, e.qty, e.production_date, e.shift,
               e.operator_name, e.notes, e.source,
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
    entry = db.execute(text("""
        SELECT id, line_id, production_date, shift
        FROM scrap_entries
        WHERE id=:id
        FOR UPDATE
    """), {"id": entry_id}).fetchone()
    if not entry:
        raise HTTPException(404, "Kayıt bulunamadı")

    try:
        validate_production_date(entry.production_date)
    except ScrapDateError as exc:
        db.rollback()
        raise HTTPException(403, str(exc)) from exc

    try:
        db.execute(
            text("DELETE FROM scrap_entries WHERE id=:id"),
            {"id": entry_id},
        )
        recalculated = recalculate_existing_summary(
            db,
            line_id=entry.line_id,
            production_date=entry.production_date,
            shift=entry.shift,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception(
            "Fire silme/özet güncellemesi başarısız: entry_id=%s",
            entry_id,
        )
        raise HTTPException(
            409,
            "Fire silme ve vardiya özeti birlikte güncellenemedi; "
            "hiçbir değişiklik kaydedilmedi",
        ) from exc
    return {"deleted": entry_id, **summary_response(recalculated)}


# ─── 6) ANALİZ (sebep bazlı + günlük) ────────
@router.get("/analysis")
def scrap_analysis(
    line_id: Optional[int] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    shift: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    clauses, params = ["e.qty > 0"], {}
    if line_id:
        clauses.append("e.line_id = :lid"); params["lid"] = line_id
    if shift:
        clauses.append("e.shift = :shift"); params["shift"] = shift
    if date_from:
        clauses.append("e.production_date >= :df"); params["df"] = date_from
    if date_to:
        clauses.append("e.production_date <= :dt"); params["dt"] = date_to
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
        SELECT e.production_date AS d, r.reason_code, COALESCE(SUM(e.qty), 0) AS qty
        FROM scrap_entries e
        JOIN scrap_reasons r ON e.reason_id = r.reason_id
        {where}
        GROUP BY e.production_date, r.reason_code
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
