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

router = APIRouter(prefix="/api/v1/report", tags=["Rapor"])


def _hhmm(ts):
    return ts.strftime("%H:%M") if ts else ""


@router.get("/vardiya-excel")
def vardiya_excel(
    start: date = Query(..., description="YYYY-MM-DD"),
    end: date = Query(..., description="YYYY-MM-DD"),
    line_id: int = Query(1),
    db: Session = Depends(get_db),
):
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
            WHERE d.line_id = :lid
              AND d.started_at::date = :d
              AND r.reason_code <> 'VARDIYA_SONU'
            ORDER BY d.started_at
        """), {"lid": line_id, "d": sm["shift_date"]}).fetchall()

        pairs, lost = [], 0
        for dd in dts:
            d = dict(dd._mapping)
            if d["ended_at"]:
                zaman = f"{_hhmm(d['started_at'])}-{_hhmm(d['ended_at'])}"
            else:
                zaman = f"{_hhmm(d['started_at'])}-..."
            lost += (d["duration_sec"] or 0) // 60
            pairs.append((zaman, d["reason_name"]))

        rows.append({
            "date": sm["shift_date"].strftime("%-d.%m.%Y"),
            "shift": sm["shift"],
            "target": sm["target"],
            "total_produced": sm["total_produced"],
            "total_scrap": sm["total_scrap"],
            "total_good": sm["total_good"],
            "downtimes": pairs,
            "lost_min": int(lost),
        })

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