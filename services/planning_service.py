"""Pure calculation helpers for the production planning screen.

The planning module deliberately has no write path to production/OEE tables.
This file contains deterministic stock and raw-material projections so they can
be unit-tested without a database connection.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal, ROUND_FLOOR
from typing import Mapping


def _decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value or 0))


def _number(value: Decimal) -> float:
    return round(float(value), 3)


def _date_range(start_date: date, end_date: date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def _period_summary(rows: list[dict], key_name: str, label: str) -> dict:
    first = rows[0]
    last = rows[-1]
    product_shortage_rows = [r for r in rows if r["shortage_qty"] > 0]
    raw_shortage_rows = [r for r in rows if r["raw_shortage_qty"] > 0]
    return {
        key_name: label,
        "start_date": first["date"],
        "end_date": last["date"],
        "opening_finished_stock": first["opening_finished_stock"],
        "total_order_qty": sum(r["order_qty"] for r in rows),
        "total_planned_qty": sum(r["planned_qty"] for r in rows),
        "total_feasible_qty": sum(r["feasible_qty"] for r in rows),
        "closing_finished_stock": last["closing_finished_stock"],
        "first_shortage_date": (
            product_shortage_rows[0]["date"] if product_shortage_rows else None
        ),
        "max_shortage_qty": max(
            (r["shortage_qty"] for r in rows), default=0
        ),
        "opening_raw_stock": first["opening_raw_stock"],
        "total_raw_receipt_qty": round(
            sum(r["raw_receipt_qty"] for r in rows), 3
        ),
        "total_raw_required_qty": round(
            sum(r["planned_raw_required_qty"] for r in rows), 3
        ),
        "total_raw_consumed_qty": round(
            sum(r["raw_consumed_qty"] for r in rows), 3
        ),
        "closing_raw_stock": last["closing_raw_stock"],
        "first_raw_shortage_date": (
            raw_shortage_rows[0]["date"] if raw_shortage_rows else None
        ),
        "max_raw_shortage_qty": round(
            max((r["raw_shortage_qty"] for r in rows), default=0), 3
        ),
    }


def aggregate_periods(daily_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Aggregate daily rows into ISO weeks and calendar months.

    Period closing balances are never independently recalculated; the last
    daily balance is used so backlog and stock carry correctly across periods.
    """
    weeks: OrderedDict[tuple[int, int], list[dict]] = OrderedDict()
    months: OrderedDict[tuple[int, int], list[dict]] = OrderedDict()
    for row in daily_rows:
        d = date.fromisoformat(row["date"])
        iso = d.isocalendar()
        weeks.setdefault((iso.year, iso.week), []).append(row)
        months.setdefault((d.year, d.month), []).append(row)

    weekly = []
    for (year, week), rows in weeks.items():
        weekly.append(
            _period_summary(rows, "week", f"{year}-W{week:02d}")
        )
    monthly = []
    for (year, month), rows in months.items():
        monthly.append(
            _period_summary(rows, "month", f"{year}-{month:02d}")
        )
    return weekly, monthly


def build_forecast(
    *,
    start_date: date,
    end_date: date,
    opening_finished_stock: int,
    opening_raw_stock,
    raw_per_piece,
    raw_scrap_pct,
    finished_safety_stock: int,
    raw_safety_stock,
    order_by_date: Mapping[date, int],
    plan_by_date: Mapping[date, dict],
    receipt_by_date: Mapping[date, object],
) -> dict:
    """Build daily, weekly and monthly stock projections.

    Same-day feasible production is available for the same day's order. When
    raw consumption is not configured (zero), production is not restricted and
    raw shortage fields remain zero.
    """
    if end_date < start_date:
        raise ValueError("end_date start_date'ten önce olamaz")
    if (end_date - start_date).days > 730:
        raise ValueError("Stok devri dahil hesaplama aralığı en fazla 731 gün olabilir")

    finished_balance = int(opening_finished_stock)
    raw_balance = _decimal(opening_raw_stock)
    raw_per_piece_value = _decimal(raw_per_piece)
    scrap_multiplier = Decimal("1") + (_decimal(raw_scrap_pct) / Decimal("100"))
    effective_raw_per_piece = raw_per_piece_value * scrap_multiplier
    raw_configured = effective_raw_per_piece > 0
    raw_safety = _decimal(raw_safety_stock)

    daily = []
    for d in _date_range(start_date, end_date):
        order_qty = max(0, int(order_by_date.get(d, 0) or 0))
        plan_info = plan_by_date.get(d, {})
        planned_qty = max(0, int(plan_info.get("qty", 0) or 0))
        raw_receipt = max(Decimal("0"), _decimal(receipt_by_date.get(d, 0)))

        opening_finished = finished_balance
        opening_raw = raw_balance
        raw_available = raw_balance + raw_receipt
        planned_raw_required = (
            effective_raw_per_piece * planned_qty
            if raw_configured else Decimal("0")
        )
        raw_shortage = (
            max(Decimal("0"), planned_raw_required - raw_available)
            if raw_configured else Decimal("0")
        )
        if raw_configured:
            feasible_qty = min(
                planned_qty,
                int((raw_available / effective_raw_per_piece).to_integral_value(
                    rounding=ROUND_FLOOR
                )),
            )
        else:
            feasible_qty = planned_qty

        raw_consumed = (
            effective_raw_per_piece * feasible_qty
            if raw_configured else Decimal("0")
        )
        raw_balance = raw_available - raw_consumed
        finished_balance = opening_finished + feasible_qty - order_qty
        shortage_qty = max(0, -finished_balance)

        if shortage_qty > 0:
            status = "red"
        elif raw_shortage > 0:
            status = "orange"
        elif (
            finished_balance <= int(finished_safety_stock)
            or (raw_configured and raw_balance <= raw_safety)
        ):
            status = "yellow"
        else:
            status = "green"

        daily.append({
            "date": d.isoformat(),
            "order_qty": order_qty,
            "planned_qty": planned_qty,
            "capacity_source": plan_info.get("source", "none"),
            "shift_count": int(plan_info.get("shift_count", 0) or 0),
            "feasible_qty": feasible_qty,
            "opening_finished_stock": opening_finished,
            "closing_finished_stock": finished_balance,
            "shortage_qty": shortage_qty,
            "raw_configured": raw_configured,
            "opening_raw_stock": _number(opening_raw),
            "raw_receipt_qty": _number(raw_receipt),
            "planned_raw_required_qty": _number(planned_raw_required),
            "raw_consumed_qty": _number(raw_consumed),
            "closing_raw_stock": _number(raw_balance),
            "raw_shortage_qty": _number(raw_shortage),
            "status": status,
        })

    weekly, monthly = aggregate_periods(daily)
    return {"daily": daily, "weekly": weekly, "monthly": monthly}
