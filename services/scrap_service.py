"""Geçmiş fire kaydı ve vardiya özeti eşzamanlama yardımcıları."""

from __future__ import annotations

from datetime import date

MAX_RETROACTIVE_DAYS = 7


class ScrapDateError(ValueError):
    """Fire üretim tarihi izin verilen aralığın dışındaysa yükseltilir."""


class ScrapShiftError(ValueError):
    """Seçilen vardiya tarih/hat için bulunamazsa yükseltilir."""


def validate_production_date(
    production_date: date | None,
    *,
    today: date | None = None,
    max_days: int = MAX_RETROACTIVE_DAYS,
) -> date:
    """Bugün ile son ``max_days`` gün arasındaki üretim tarihini doğrular."""
    today = today or date.today()
    production_date = production_date or today
    age_days = (today - production_date).days
    if age_days < 0:
        raise ScrapDateError("Gelecek tarih için fire girilemez")
    if age_days > max_days:
        raise ScrapDateError(
            f"Fire en fazla {max_days} gün geriye girilebilir"
        )
    return production_date


def resolve_shift_code(
    *,
    line_id: int,
    production_date: date,
    requested_shift: str | None,
) -> tuple[str, list[dict]]:
    """Tarih/hat için vardiyayı doğrular; tek vardiyada güvenle otomatik seçer."""
    import shift_utils

    shifts = shift_utils.get_shifts(line_id=line_id, dt=production_date)
    if not shifts:
        raise ScrapShiftError("Bu tarih ve hat için aktif vardiya bulunamadı")

    if requested_shift:
        shift = next(
            (item for item in shifts if item["code"] == requested_shift),
            None,
        )
        if shift is None:
            raise ScrapShiftError(
                f"Vardiya bulunamadı: {requested_shift} / {production_date}"
            )
        return shift["code"], shifts

    if len(shifts) == 1:
        return shifts[0]["code"], shifts

    raise ScrapShiftError("Birden fazla vardiya var; vardiya seçilmelidir")


def recalculate_existing_summary(
    db,
    *,
    line_id: int,
    production_date: date,
    shift: str,
) -> dict | None:
    """Fireye bağlı alanları yeniler; tarihsel süre bileşenlerini korur.

    Geçmiş bir fire kaydı kullanılabilirlik, performans, vardiya süresi,
    duruşlar veya molaları değiştirmez. Bu nedenle vardiya özetini güncel
    vardiya ayarlarıyla baştan hesaplamak tarihsel sonuçları bozardı.
    """
    from sqlalchemy import text

    row = db.execute(text("""
        SELECT total_produced, oee_availability, oee_performance,
               formula_version
        FROM shift_summary
        WHERE line_id=:line_id
          AND shift_date=:production_date
          AND shift=:shift
        FOR UPDATE
    """), {
        "line_id": line_id,
        "production_date": production_date,
        "shift": shift,
    }).fetchone()
    if row is None:
        return None

    values = dict(row._mapping) if hasattr(row, "_mapping") else {
        "total_produced": row[0],
        "oee_availability": row[1],
        "oee_performance": row[2],
        "formula_version": row[3],
    }
    scrap_row = db.execute(text("""
        SELECT COALESCE(SUM(qty), 0)
        FROM scrap_entries
        WHERE line_id=:line_id
          AND production_date=:production_date
          AND shift=:shift
    """), {
        "line_id": line_id,
        "production_date": production_date,
        "shift": shift,
    }).fetchone()

    produced = max(int(values["total_produced"] or 0), 0)
    scrap = max(int(scrap_row[0] or 0), 0)
    if scrap > produced:
        raise ValueError(
            "Vardiya toplam firesi toplam üretimden büyük olamaz"
        )

    good = produced - scrap
    quality = round((good / produced) * 100, 1) if produced else 0.0
    availability = float(values["oee_availability"] or 0)
    performance = float(values["oee_performance"] or 0)
    overall = round(
        availability * performance * quality / 10000,
        1,
    )

    result = {
        "line_id": line_id,
        "shift_date": production_date,
        "shift": shift,
        "total_produced": produced,
        "total_scrap": scrap,
        "total_good": good,
        "oee_quality": quality,
        "oee_overall": overall,
        "formula_version": values["formula_version"],
    }
    db.execute(text("""
        UPDATE shift_summary
        SET total_scrap=:total_scrap,
            total_good=:total_good,
            oee_quality=:oee_quality,
            oee_overall=:oee_overall,
            calculated_at=NOW()
        WHERE line_id=:line_id
          AND shift_date=:production_date
          AND shift=:shift
    """), {
        **result,
        "production_date": production_date,
    })
    return result


def historical_shift_options(
    db,
    *,
    line_id: int,
    production_date: date,
    resolved_shifts: list[dict],
) -> list[dict]:
    """Tamamlanmış vardiyada kaydedilmiş brüt süreyi ekranda korur."""
    from sqlalchemy import text

    rows = db.execute(text("""
        SELECT shift, planned_base_sec
        FROM shift_summary
        WHERE line_id=:line_id
          AND shift_date=:production_date
        ORDER BY shift
    """), {
        "line_id": line_id,
        "production_date": production_date,
    }).fetchall()
    if not rows:
        return resolved_shifts

    resolved_by_code = {item["code"]: item for item in resolved_shifts}
    options = []
    for row in rows:
        values = dict(row._mapping) if hasattr(row, "_mapping") else {
            "shift": row[0],
            "planned_base_sec": row[1],
        }
        code = values["shift"]
        base = dict(resolved_by_code.get(code, {}))
        start_hour = int(base.get("start_hour", 0))
        duration_hours = int(values["planned_base_sec"] or 0) // 3600
        base.update({
            "code": code,
            "label": base.get("label", code),
            "start_hour": start_hour,
            "end_hour": (start_hour + duration_hours) % 24,
            "source": "saved_summary",
        })
        options.append(base)
    return options


def summary_response(result: dict | None) -> dict:
    if result is None:
        return {"summary_recalculated": False, "summary": None}
    return {
        "summary_recalculated": True,
        "summary": {
            "line_id": result["line_id"],
            "shift_date": str(result["shift_date"]),
            "shift": result["shift"],
            "total_produced": result["total_produced"],
            "total_scrap": result["total_scrap"],
            "total_good": result["total_good"],
            "oee_quality": result["oee_quality"],
            "oee_overall": result["oee_overall"],
            "formula_version": result["formula_version"],
        },
    }
