"""
Raporlama API Router
routers/reports.py

  GET /api/v1/report/vardiya-excel?start=YYYY-MM-DD&end=YYYY-MM-DD[&line_id=1]
      → Vardiya raporu Excel (operatör formu düzeni)

DB bağlantısı production.py ile aynı: models.database.get_db (şifre dosyada tutulmaz).
main.py'de prefix'siz include edilir (prefix router'ın içinde):
    app.include_router(reports.router)
"""
from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from models.database import get_db
from services.shift_report_xlsx import build_report
import zipfile
from io import BytesIO
from zoneinfo import ZoneInfo
TZ = ZoneInfo("Europe/Istanbul")
router = APIRouter(prefix="/api/v1/report", tags=["Rapor"])


def _hhmm(ts):
    if not ts:
        return ""
    if ts.tzinfo is None:                     # naive gelirse UTC varsay
        ts = ts.replace(tzinfo=ZoneInfo("UTC"))
    return ts.astimezone(TZ).strftime("%H:%M")

def _line_name(db, line_id: int) -> str:
    row = db.execute(text(
        "SELECT line_name FROM production_lines WHERE line_id = :lid"
    ), {"lid": line_id}).fetchone()
    return row[0] if row else f"Hat {line_id}"

def _build_line_rows(db, line_id: int, start: date, end: date) -> list:
    """Bir hat için vardiya + duruş satırlarını üretir (build_report girdisi)."""
    summaries = db.execute(text("""
        SELECT shift_date, shift, target, total_produced, total_scrap, total_good
        FROM shift_summary
        WHERE line_id = :lid AND shift_date BETWEEN :start AND :end
        ORDER BY shift_date, shift
    """), {"lid": line_id, "start": start, "end": end}).fetchall()

    rows = []
    for s in summaries:
        sm = dict(s._mapping)
        dts = db.execute(text("""
            SELECT d.started_at, d.ended_at, d.duration_sec, r.reason_name
            FROM downtimes d
            JOIN downtime_reasons r ON r.reason_id = d.reason_id
            WHERE d.line_id = :lid AND d.started_at::date = :d
              AND r.reason_code <> 'VARDIYA_SONU'
            ORDER BY d.started_at
        """), {"lid": line_id, "d": sm["shift_date"]}).fetchall()

        pairs, lost = [], 0
        for dd in dts:
            d = dict(dd._mapping)
            zaman = f"{_hhmm(d['started_at'])}-{_hhmm(d['ended_at'])}" if d["ended_at"] \
                    else f"{_hhmm(d['started_at'])}-..."
            lost += (d["duration_sec"] or 0) // 60
            pairs.append((zaman, d["reason_name"]))

        rows.append({
            "date": sm["shift_date"].strftime("%-d.%m.%Y"),
            "shift": sm["shift"], "target": sm["target"],
            "total_produced": sm["total_produced"], "total_scrap": sm["total_scrap"],
            "total_good": sm["total_good"], "downtimes": pairs, "lost_min": int(lost),
        })
    return rows
@router.get("/vardiya-excel")
def vardiya_excel(start: date = Query(...), end: date = Query(...),
                  line_id: int = Query(1), db: Session = Depends(get_db)):
    rows = _build_line_rows(db, line_id, start, end)
    if not rows:
        rows = [{"date": start.strftime("%-d.%m.%Y"), "shift": "vardiya_1",
                 "target": None, "total_produced": None, "total_scrap": None,
                 "total_good": None, "downtimes": [], "lost_min": 0}]
    data = build_report(rows, line_name=_line_name(db, line_id))
    fname = f"vardiya_raporu_hat{line_id}_{start}_{end}.xlsx"
    return StreamingResponse(iter([data]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})

    if not rows:
        rows = [{"date": start.strftime("%-d.%m.%Y"), "shift": "vardiya_1",
                 "target": None, "total_produced": None, "total_scrap": None,
                 "total_good": None, "downtimes": [], "lost_min": 0}]

    data = build_report(rows)
    fname = f"vardiya_raporu_{start}_{end}.xlsx"
    return StreamingResponse(
        iter([data]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
@router.get("/vardiya-excel-all")
def vardiya_excel_all(start: date = Query(...), end: date = Query(...),
                      db: Session = Depends(get_db)):
    lines = db.execute(text("""
        SELECT line_id, line_name FROM production_lines
        WHERE is_active = TRUE ORDER BY line_id
    """)).fetchall()

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for ln in lines:
            lid, lname = ln[0], ln[1]
            rows = _build_line_rows(db, lid, start, end)
            if not rows:
                continue                      # veri yoksa o hattı atla
            xlsx = build_report(rows, line_name=lname)
            safe = lname.replace(" ", "_").replace("/", "-")
            z.writestr(f"vardiya_{safe}_{start}_{end}.xlsx", xlsx)

    buf.seek(0)
    fname = f"vardiya_raporu_tum_hatlar_{start}_{end}.zip"
    return StreamingResponse(iter([buf.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})