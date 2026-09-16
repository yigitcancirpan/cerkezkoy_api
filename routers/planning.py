"""Production planning API for World Hattı / product 2962070700.

This router owns only ``planning_*`` tables. Existing production, OEE,
downtime and scrap tables are read-only inputs and are never mutated here.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from models.database import get_db
from services.planning_service import aggregate_periods, build_forecast


router = APIRouter(prefix="/api/v1/planning", tags=["Üretim Planlama"])

PLANNING_LINE_ID = 2
PLANNING_PRODUCT_CODE = "2962070700"
MAX_BULK_ITEMS = 400
RAW_ORDER_STATUSES = {"planned", "confirmed", "received", "cancelled"}


class PlanningConfigUpdate(BaseModel):
    finished_stock: int = Field(ge=0)
    finished_stock_as_of: date
    finished_safety_stock: int = Field(default=0, ge=0)
    raw_material_code: Optional[str] = Field(default=None, max_length=80)
    raw_material_name: Optional[str] = Field(default=None, max_length=150)
    raw_unit: str = Field(default="kg", min_length=1, max_length=20)
    raw_stock_qty: Decimal = Field(default=Decimal("0"), ge=0)
    raw_stock_as_of: date
    raw_per_piece: Decimal = Field(default=Decimal("0"), ge=0)
    raw_scrap_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    raw_safety_stock: Decimal = Field(default=Decimal("0"), ge=0)
    workdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5])


class FinishedStockUpdate(BaseModel):
    finished_stock: int = Field(ge=0)
    finished_stock_as_of: date
    finished_safety_stock: int = Field(default=0, ge=0)


class RawMaterialUpdate(BaseModel):
    raw_material_code: Optional[str] = Field(default=None, max_length=80)
    raw_material_name: Optional[str] = Field(default=None, max_length=150)
    raw_unit: str = Field(default="kg", min_length=1, max_length=20)
    raw_stock_qty: Decimal = Field(default=Decimal("0"), ge=0)
    raw_stock_as_of: date
    raw_per_piece: Decimal = Field(default=Decimal("0"), ge=0)
    raw_scrap_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    raw_safety_stock: Decimal = Field(default=Decimal("0"), ge=0)


class WorkdaysUpdate(BaseModel):
    workdays: list[int]


class RawOrderCreate(BaseModel):
    expected_date: date
    order_qty: Decimal = Field(gt=0)
    batch_ref: Optional[str] = Field(default=None, max_length=100)
    status: str = Field(default="planned", max_length=20)
    notes: Optional[str] = Field(default=None, max_length=300)


class DatedQuantity(BaseModel):
    target_date: date
    qty: Decimal = Field(ge=0)
    notes: Optional[str] = Field(default=None, max_length=300)


class BulkQuantities(BaseModel):
    items: list[DatedQuantity]


class WeeklyCapacityUpdate(BaseModel):
    week_start: date
    daily_default_qty: int = Field(ge=0)


class DailyCapacityUpdate(BaseModel):
    plan_date: date
    planned_qty: int = Field(ge=0)


def _require_scope(line_id: int, product_code: str) -> None:
    if line_id != PLANNING_LINE_ID or not product_code or len(product_code) > 50:
        raise HTTPException(
            422,
            "World Hattı ve geçerli bir malzeme kodu seçin",
        )


def _validate_items(items: list[DatedQuantity]) -> None:
    if not items:
        raise HTTPException(422, "En az bir kayıt gönderilmeli")
    if len(items) > MAX_BULK_ITEMS:
        raise HTTPException(422, f"Tek istekte en fazla {MAX_BULK_ITEMS} kayıt")
    dates = [item.target_date for item in items]
    if len(dates) != len(set(dates)):
        raise HTTPException(422, "Aynı tarih istekte birden fazla kez gönderilemez")


def _validate_workdays(workdays: list[int]) -> list[int]:
    result = sorted(set(workdays))
    if any(day < 0 or day > 6 for day in result):
        raise HTTPException(422, "Çalışma günleri 0–6 aralığında olmalı")
    return result


def _validate_raw_order_status(status: str) -> str:
    value = status.strip().lower()
    if value not in RAW_ORDER_STATUSES:
        raise HTTPException(
            422,
            "Hammadde sipariş durumu planned, confirmed, received veya cancelled olmalı",
        )
    return value


def _config_row(db: Session, product_code: str = PLANNING_PRODUCT_CODE):
    try:
        row = db.execute(text("""
            SELECT line_id, product_code, finished_stock,
                   finished_stock_as_of, finished_safety_stock,
                   raw_material_code, raw_material_name, raw_unit,
                   raw_stock_qty, raw_stock_as_of, raw_per_piece,
                   raw_scrap_pct, raw_safety_stock, workdays, updated_at
            FROM planning_product_settings
            WHERE line_id = :lid AND product_code = :code
        """), {"lid": PLANNING_LINE_ID, "code": product_code}).fetchone()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            503,
            "Planlama tabloları hazır değil; stage1_06 migration uygulanmalı",
        ) from exc
    if not row:
        raise HTTPException(503, "Planlama ürün ayarı bulunamadı")
    return row


def _product_meta(db: Session, product_code: str = PLANNING_PRODUCT_CODE) -> dict:
    material = db.execute(text("""
        SELECT material_id, material_code, material_name, model_id,
               ideal_cycle_ds, default_target
        FROM materials
        WHERE material_code = :code AND is_active = TRUE
        LIMIT 1
    """), {"code": product_code}).fetchone()
    if not material:
        raise HTTPException(409, "Aktif malzeme kaydı bulunamadı")
    line = db.execute(text("""
        SELECT l.line_id, l.line_name, l.site_id, s.site_code, s.site_name
        FROM production_lines l
        JOIN sites s ON s.site_id = l.site_id
        WHERE l.line_id = :lid AND l.is_active = TRUE
    """), {"lid": PLANNING_LINE_ID}).fetchone()
    if not line:
        raise HTTPException(409, "World Hattı bulunamadı")
    return {
        "line": dict(line._mapping),
        "material": dict(material._mapping),
    }


def _current_target(db: Session, model_id: Optional[int]) -> int:
    row = db.execute(text("""
        SELECT target, model_id
        FROM production_current
        WHERE line_id = :lid
    """), {"lid": PLANNING_LINE_ID}).fetchone()
    if not row or int(row.target or 0) <= 0:
        return 0
    if model_id is not None and int(row.model_id or 0) != int(model_id):
        return 0
    return int(row.target)


def _default_plan_for_date(
    target_date: date,
    workdays: set[int],
    target_per_shift: int,
    ideal_cycle_ds: Optional[int],
) -> dict:
    if target_date.weekday() not in workdays:
        return {"qty": 0, "source": "non_workday", "shift_count": 0}

    import shift_utils
    shifts = shift_utils.get_shifts(
        line_id=PLANNING_LINE_ID,
        dt=target_date,
    )
    shift_count = len(shifts)
    if target_per_shift > 0:
        return {
            "qty": target_per_shift * shift_count,
            "source": "target_and_shifts",
            "shift_count": shift_count,
        }
    if ideal_cycle_ds and ideal_cycle_ds > 0:
        total_seconds = sum(int(s.get("planned_seconds") or 0) for s in shifts)
        return {
            "qty": int(total_seconds / (ideal_cycle_ds / 10.0)),
            "source": "shift_and_ideal_cycle",
            "shift_count": shift_count,
        }
    return {"qty": 0, "source": "not_configured", "shift_count": shift_count}


@router.get("/config")
def get_config(
    line_id: int = Query(PLANNING_LINE_ID),
    product_code: str = Query(PLANNING_PRODUCT_CODE),
    db: Session = Depends(get_db),
):
    _require_scope(line_id, product_code)
    config = dict(_config_row(db, product_code)._mapping)
    meta = _product_meta(db, product_code)
    return {"config": config, **meta}


@router.put("/config")
def update_config(
    body: PlanningConfigUpdate,
    line_id: int = Query(PLANNING_LINE_ID),
    product_code: str = Query(PLANNING_PRODUCT_CODE),
    db: Session = Depends(get_db),
):
    _require_scope(line_id, product_code)
    workdays = _validate_workdays(body.workdays)
    db.execute(text("""
        UPDATE planning_product_settings
        SET finished_stock = :finished_stock,
            finished_stock_as_of = :finished_as_of,
            finished_safety_stock = :finished_safety,
            raw_material_code = :raw_code,
            raw_material_name = :raw_name,
            raw_unit = :raw_unit,
            raw_stock_qty = :raw_stock,
            raw_stock_as_of = :raw_as_of,
            raw_per_piece = :raw_per_piece,
            raw_scrap_pct = :raw_scrap_pct,
            raw_safety_stock = :raw_safety,
            workdays = CAST(:workdays AS jsonb),
            updated_at = NOW()
        WHERE line_id = :lid AND product_code = :code
    """), {
        "finished_stock": body.finished_stock,
        "finished_as_of": body.finished_stock_as_of,
        "finished_safety": body.finished_safety_stock,
        "raw_code": (body.raw_material_code or "").strip() or None,
        "raw_name": (body.raw_material_name or "").strip() or None,
        "raw_unit": body.raw_unit.strip(),
        "raw_stock": body.raw_stock_qty,
        "raw_as_of": body.raw_stock_as_of,
        "raw_per_piece": body.raw_per_piece,
        "raw_scrap_pct": body.raw_scrap_pct,
        "raw_safety": body.raw_safety_stock,
        "workdays": json.dumps(workdays),
        "lid": line_id,
        "code": product_code,
    })
    db.commit()
    return {"message": "Planlama ayarları güncellendi"}


@router.put("/finished-stock")
def update_finished_stock(
    body: FinishedStockUpdate,
    line_id: int = Query(PLANNING_LINE_ID),
    product_code: str = Query(PLANNING_PRODUCT_CODE),
    db: Session = Depends(get_db),
):
    """Update only the finished-goods opening balance."""
    _require_scope(line_id, product_code)
    _config_row(db, product_code)
    db.execute(text("""
        UPDATE planning_product_settings
        SET finished_stock=:stock,
            finished_stock_as_of=:as_of,
            finished_safety_stock=:safety,
            updated_at=NOW()
        WHERE line_id=:lid AND product_code=:code
    """), {
        "stock": body.finished_stock,
        "as_of": body.finished_stock_as_of,
        "safety": body.finished_safety_stock,
        "lid": line_id,
        "code": product_code,
    })
    db.commit()
    return {"message": "Mamul stok bilgisi güncellendi"}


@router.put("/raw-material")
def update_raw_material(
    body: RawMaterialUpdate,
    line_id: int = Query(PLANNING_LINE_ID),
    product_code: str = Query(PLANNING_PRODUCT_CODE),
    db: Session = Depends(get_db),
):
    """Update only raw-material identity, balance and consumption settings."""
    _require_scope(line_id, product_code)
    _config_row(db, product_code)
    db.execute(text("""
        UPDATE planning_product_settings
        SET raw_material_code=:raw_code,
            raw_material_name=:raw_name,
            raw_unit=:raw_unit,
            raw_stock_qty=:raw_stock,
            raw_stock_as_of=:raw_as_of,
            raw_per_piece=:raw_per_piece,
            raw_scrap_pct=:raw_scrap_pct,
            raw_safety_stock=:raw_safety,
            updated_at=NOW()
        WHERE line_id=:lid AND product_code=:code
    """), {
        "raw_code": (body.raw_material_code or "").strip() or None,
        "raw_name": (body.raw_material_name or "").strip() or None,
        "raw_unit": body.raw_unit.strip(),
        "raw_stock": body.raw_stock_qty,
        "raw_as_of": body.raw_stock_as_of,
        "raw_per_piece": body.raw_per_piece,
        "raw_scrap_pct": body.raw_scrap_pct,
        "raw_safety": body.raw_safety_stock,
        "lid": line_id,
        "code": product_code,
    })
    db.commit()
    return {"message": "Hammadde stok ve tüketim bilgisi güncellendi"}


@router.put("/workdays")
def update_workdays(
    body: WorkdaysUpdate,
    line_id: int = Query(PLANNING_LINE_ID),
    product_code: str = Query(PLANNING_PRODUCT_CODE),
    db: Session = Depends(get_db),
):
    _require_scope(line_id, product_code)
    workdays = _validate_workdays(body.workdays)
    _config_row(db, product_code)
    db.execute(text("""
        UPDATE planning_product_settings
        SET workdays=CAST(:workdays AS jsonb), updated_at=NOW()
        WHERE line_id=:lid AND product_code=:code
    """), {
        "workdays": json.dumps(workdays),
        "lid": line_id,
        "code": product_code,
    })
    db.commit()
    return {"message": "Çalışma günleri güncellendi"}


@router.put("/orders/bulk")
def upsert_orders(
    body: BulkQuantities,
    line_id: int = Query(PLANNING_LINE_ID),
    product_code: str = Query(PLANNING_PRODUCT_CODE),
    db: Session = Depends(get_db),
):
    _require_scope(line_id, product_code)
    _validate_items(body.items)
    for item in body.items:
        qty = int(item.qty)
        if item.qty != qty:
            raise HTTPException(422, "Sipariş adedi tam sayı olmalı")
        if qty == 0:
            db.execute(text("""
                DELETE FROM planning_orders
                WHERE line_id=:lid AND product_code=:code AND order_date=:d
            """), {"lid": line_id, "code": product_code, "d": item.target_date})
        else:
            db.execute(text("""
                INSERT INTO planning_orders
                    (line_id, product_code, order_date, order_qty, notes, updated_at)
                VALUES (:lid,:code,:d,:qty,:notes,NOW())
                ON CONFLICT (line_id, product_code, order_date)
                DO UPDATE SET order_qty=EXCLUDED.order_qty,
                              notes=EXCLUDED.notes, updated_at=NOW()
            """), {"lid": line_id, "code": product_code,
                     "d": item.target_date, "qty": qty, "notes": item.notes})
    db.commit()
    return {"updated": len(body.items)}


@router.put("/weekly-capacity")
def upsert_weekly_capacity(
    body: WeeklyCapacityUpdate,
    line_id: int = Query(PLANNING_LINE_ID),
    product_code: str = Query(PLANNING_PRODUCT_CODE),
    db: Session = Depends(get_db),
):
    _require_scope(line_id, product_code)
    if body.week_start.weekday() != 0:
        raise HTTPException(422, "Hafta başlangıcı pazartesi olmalı")
    db.execute(text("""
        INSERT INTO planning_weekly_capacity
            (line_id, product_code, week_start, daily_default_qty, updated_at)
        VALUES (:lid,:code,:d,:qty,NOW())
        ON CONFLICT (line_id, product_code, week_start)
        DO UPDATE SET daily_default_qty=EXCLUDED.daily_default_qty, updated_at=NOW()
    """), {"lid": line_id, "code": product_code,
             "d": body.week_start, "qty": body.daily_default_qty})
    db.commit()
    return {"message": "Haftalık günlük varsayılan kaydedildi"}


@router.delete("/weekly-capacity/{week_start}")
def delete_weekly_capacity(
    week_start: date,
    line_id: int = Query(PLANNING_LINE_ID),
    product_code: str = Query(PLANNING_PRODUCT_CODE),
    db: Session = Depends(get_db),
):
    _require_scope(line_id, product_code)
    db.execute(text("""
        DELETE FROM planning_weekly_capacity
        WHERE line_id=:lid AND product_code=:code AND week_start=:d
    """), {"lid": line_id, "code": product_code, "d": week_start})
    db.commit()
    return {"message": "Haftalık özel değer kaldırıldı"}


@router.put("/daily-capacity")
def upsert_daily_capacity(
    body: DailyCapacityUpdate,
    line_id: int = Query(PLANNING_LINE_ID),
    product_code: str = Query(PLANNING_PRODUCT_CODE),
    db: Session = Depends(get_db),
):
    _require_scope(line_id, product_code)
    db.execute(text('LOCK TABLE planning_daily_capacity IN SHARE ROW EXCLUSIVE MODE'))
    if body.planned_qty > 0 and db.execute(text('''
        SELECT 1 FROM planning_daily_capacity WHERE line_id=:lid
        AND product_code<>:code AND plan_date=:d AND planned_qty>0 LIMIT 1
    '''), {'lid': line_id, 'code': product_code, 'd': body.plan_date}).fetchone():
        raise HTTPException(409, 'Bu gün aynı hatta başka malzemenin günlük üretim planı var.')
    db.execute(text("""
        INSERT INTO planning_daily_capacity
            (line_id, product_code, plan_date, planned_qty, updated_at)
        VALUES (:lid,:code,:d,:qty,NOW())
        ON CONFLICT (line_id, product_code, plan_date)
        DO UPDATE SET planned_qty=EXCLUDED.planned_qty, updated_at=NOW(),
                      auto_run_id=NULL, shift_code=NULL, shift_label=NULL,
                      shift_hours=NULL, is_locked=FALSE,
                      break_work=FALSE, break_work_minutes=0, break_extra_qty=0
    """), {"lid": line_id, "code": product_code,
             "d": body.plan_date, "qty": body.planned_qty})
    db.commit()
    return {"message": "Günlük plan kaydedildi"}


@router.delete("/daily-capacity/{plan_date}")
def delete_daily_capacity(
    plan_date: date,
    line_id: int = Query(PLANNING_LINE_ID),
    product_code: str = Query(PLANNING_PRODUCT_CODE),
    db: Session = Depends(get_db),
):
    _require_scope(line_id, product_code)
    db.execute(text("""
        DELETE FROM planning_daily_capacity
        WHERE line_id=:lid AND product_code=:code AND plan_date=:d
    """), {"lid": line_id, "code": product_code, "d": plan_date})
    db.commit()
    return {"message": "Günlük özel değer kaldırıldı"}


@router.put("/raw-receipts/bulk")
def upsert_raw_receipts(
    body: BulkQuantities,
    line_id: int = Query(PLANNING_LINE_ID),
    product_code: str = Query(PLANNING_PRODUCT_CODE),
    db: Session = Depends(get_db),
):
    _require_scope(line_id, product_code)
    _validate_items(body.items)
    for item in body.items:
        if item.qty == 0:
            db.execute(text("""
                DELETE FROM planning_raw_receipts
                WHERE line_id=:lid AND product_code=:code AND receipt_date=:d
            """), {"lid": line_id, "code": product_code, "d": item.target_date})
        else:
            db.execute(text("""
                INSERT INTO planning_raw_receipts
                    (line_id, product_code, receipt_date, receipt_qty, notes, updated_at)
                VALUES (:lid,:code,:d,:qty,:notes,NOW())
                ON CONFLICT (line_id, product_code, receipt_date)
                DO UPDATE SET receipt_qty=EXCLUDED.receipt_qty,
                              notes=EXCLUDED.notes, updated_at=NOW()
            """), {"lid": line_id, "code": product_code,
                     "d": item.target_date, "qty": item.qty, "notes": item.notes})
    db.commit()
    return {"updated": len(body.items)}


@router.get("/raw-orders")
def list_raw_orders(
    start_date: date = Query(...),
    end_date: date = Query(...),
    line_id: int = Query(PLANNING_LINE_ID),
    product_code: str = Query(PLANNING_PRODUCT_CODE),
    db: Session = Depends(get_db),
):
    _require_scope(line_id, product_code)
    if end_date < start_date:
        raise HTTPException(422, "Bitiş tarihi başlangıçtan önce olamaz")
    rows = db.execute(text("""
        SELECT raw_order_id, expected_date, order_qty, batch_ref,
               status, notes, created_at, updated_at
        FROM planning_raw_orders
        WHERE line_id=:lid AND product_code=:code
          AND expected_date BETWEEN :start AND :end
        ORDER BY expected_date, raw_order_id
    """), {
        "lid": line_id,
        "code": product_code,
        "start": start_date,
        "end": end_date,
    }).fetchall()
    return {"items": [dict(row._mapping) for row in rows]}


@router.post("/raw-orders", status_code=201)
def create_raw_order(
    body: RawOrderCreate,
    line_id: int = Query(PLANNING_LINE_ID),
    product_code: str = Query(PLANNING_PRODUCT_CODE),
    db: Session = Depends(get_db),
):
    _require_scope(line_id, product_code)
    status = _validate_raw_order_status(body.status)
    row = db.execute(text("""
        INSERT INTO planning_raw_orders
            (line_id, product_code, expected_date, order_qty,
             batch_ref, status, notes, updated_at)
        VALUES (:lid,:code,:expected,:qty,:batch,:status,:notes,NOW())
        RETURNING raw_order_id
    """), {
        "lid": line_id,
        "code": product_code,
        "expected": body.expected_date,
        "qty": body.order_qty,
        "batch": (body.batch_ref or "").strip() or None,
        "status": status,
        "notes": (body.notes or "").strip() or None,
    }).fetchone()
    db.commit()
    return {"raw_order_id": row.raw_order_id, "message": "Hammadde siparişi eklendi"}


@router.put("/raw-orders/{raw_order_id}")
def update_raw_order(
    raw_order_id: int,
    body: RawOrderCreate,
    line_id: int = Query(PLANNING_LINE_ID),
    product_code: str = Query(PLANNING_PRODUCT_CODE),
    db: Session = Depends(get_db),
):
    _require_scope(line_id, product_code)
    status = _validate_raw_order_status(body.status)
    row = db.execute(text("""
        UPDATE planning_raw_orders
        SET expected_date=:expected, order_qty=:qty, batch_ref=:batch,
            status=:status, notes=:notes, updated_at=NOW()
        WHERE raw_order_id=:id AND line_id=:lid AND product_code=:code
        RETURNING raw_order_id
    """), {
        "id": raw_order_id,
        "lid": line_id,
        "code": product_code,
        "expected": body.expected_date,
        "qty": body.order_qty,
        "batch": (body.batch_ref or "").strip() or None,
        "status": status,
        "notes": (body.notes or "").strip() or None,
    }).fetchone()
    if not row:
        db.rollback()
        raise HTTPException(404, "Hammadde siparişi bulunamadı")
    db.commit()
    return {"message": "Hammadde siparişi güncellendi"}


@router.delete("/raw-orders/{raw_order_id}")
def delete_raw_order(
    raw_order_id: int,
    line_id: int = Query(PLANNING_LINE_ID),
    product_code: str = Query(PLANNING_PRODUCT_CODE),
    db: Session = Depends(get_db),
):
    _require_scope(line_id, product_code)
    row = db.execute(text("""
        DELETE FROM planning_raw_orders
        WHERE raw_order_id=:id AND line_id=:lid AND product_code=:code
        RETURNING raw_order_id
    """), {"id": raw_order_id, "lid": line_id, "code": product_code}).fetchone()
    if not row:
        db.rollback()
        raise HTTPException(404, "Hammadde siparişi bulunamadı")
    db.commit()
    return {"message": "Hammadde siparişi silindi"}


@router.get("/board")
def planning_board(
    start_date: date = Query(...),
    end_date: date = Query(...),
    line_id: int = Query(PLANNING_LINE_ID),
    product_code: str = Query(PLANNING_PRODUCT_CODE),
    db: Session = Depends(get_db),
):
    _require_scope(line_id, product_code)
    if end_date < start_date:
        raise HTTPException(422, "Bitiş tarihi başlangıçtan önce olamaz")
    if (end_date - start_date).days > 366:
        raise HTTPException(422, "Planlama aralığı en fazla 367 gün olabilir")

    config = dict(_config_row(db, product_code)._mapping)
    calculation_start = config["finished_stock_as_of"]
    if start_date < calculation_start:
        raise HTTPException(
            422,
            "Başlangıç tarihi mamul stok tarihinden önce olamaz; "
            "yapıştırılan stok hangi güne aitse stok tarihini o gün seçin",
        )
    if (end_date - calculation_start).days > 730:
        raise HTTPException(
            422,
            "Stok tarihi çok eski; güncel stok ve stok tarihini kaydedin",
        )
    if (
        Decimal(config["raw_per_piece"] or 0) > 0
        and calculation_start != config["raw_stock_as_of"]
    ):
        raise HTTPException(
            422,
            "Hammadde hesabı açıkken mamul ve hammadde stok tarihleri "
            "aynı olmalı",
        )
    meta = _product_meta(db, product_code)
    material = meta["material"]
    target_per_shift = int(material.get("default_target") or 0)
    current_target = _current_target(db, material.get("model_id"))
    if target_per_shift <= 0:
        target_per_shift = current_target

    query_params = {
        "lid": line_id,
        "code": product_code,
        "start": calculation_start,
        "end": end_date,
    }
    orders = {
        r.order_date: int(r.order_qty)
        for r in db.execute(text("""
            SELECT order_date, order_qty FROM planning_orders
            WHERE line_id=:lid AND product_code=:code
              AND order_date BETWEEN :start AND :end
        """), query_params).fetchall()
    }
    week_start = calculation_start - timedelta(days=calculation_start.weekday())
    weekly = {
        r.week_start: int(r.daily_default_qty)
        for r in db.execute(text("""
            SELECT week_start, daily_default_qty FROM planning_weekly_capacity
            WHERE line_id=:lid AND product_code=:code
              AND week_start BETWEEN :week_start AND :end
        """), {**query_params, "week_start": week_start}).fetchall()
    }
    overrides = {
        r.plan_date: int(r.planned_qty)
        for r in db.execute(text("""
            SELECT plan_date, planned_qty FROM planning_daily_capacity
            WHERE line_id=:lid AND product_code=:code
              AND plan_date BETWEEN :start AND :end
        """), query_params).fetchall()
    }
    receipts = {
        r.receipt_date: r.receipt_qty
        for r in db.execute(text("""
            SELECT expected_date AS receipt_date, SUM(order_qty) AS receipt_qty
            FROM planning_raw_orders
            WHERE line_id=:lid AND product_code=:code
              AND expected_date BETWEEN :start AND :end
              AND status IN ('planned', 'confirmed')
            GROUP BY expected_date
        """), query_params).fetchall()
    }

    workdays = set(config.get("workdays", [0, 1, 2, 3, 4, 5]))
    plan_by_date = {}
    current = calculation_start
    while current <= end_date:
        if current in overrides:
            plan_by_date[current] = {
                "qty": overrides[current], "source": "daily_override", "shift_count": 0,
            }
        else:
            monday = current - timedelta(days=current.weekday())
            if monday in weekly and current.weekday() in workdays:
                plan_by_date[current] = {
                    "qty": weekly[monday], "source": "weekly_default", "shift_count": 0,
                }
            else:
                plan_by_date[current] = _default_plan_for_date(
                    current,
                    workdays,
                    target_per_shift,
                    material.get("ideal_cycle_ds"),
                )
        current += timedelta(days=1)

    forecast = build_forecast(
        start_date=calculation_start,
        end_date=end_date,
        opening_finished_stock=int(config["finished_stock"]),
        opening_raw_stock=config["raw_stock_qty"],
        raw_per_piece=config["raw_per_piece"],
        raw_scrap_pct=config["raw_scrap_pct"],
        finished_safety_stock=int(config["finished_safety_stock"]),
        raw_safety_stock=config["raw_safety_stock"],
        order_by_date=orders,
        plan_by_date=plan_by_date,
        receipt_by_date=receipts,
    )
    if calculation_start < start_date:
        forecast["daily"] = [
            row for row in forecast["daily"] if row["date"] >= start_date.isoformat()
        ]
        forecast["weekly"], forecast["monthly"] = aggregate_periods(
            forecast["daily"]
        )

    return {
        "scope": {
            "line_id": line_id,
            "line_name": meta["line"]["line_name"],
            "site_code": meta["line"]["site_code"],
            "site_name": meta["line"]["site_name"],
            "product_code": product_code,
            "product_name": material["material_name"],
        },
        "config": config,
        "capacity_defaults": {
            "material_default_target": int(material.get("default_target") or 0),
            "current_target": current_target,
            "ideal_cycle_sec": (
                float(material["ideal_cycle_ds"]) / 10
                if material.get("ideal_cycle_ds") else None
            ),
        },
        **forecast,
    }


# Additive planning-only endpoints; no production/OEE router changes.
from routers.auto_planning import router as auto_planning_router
router.include_router(auto_planning_router)
