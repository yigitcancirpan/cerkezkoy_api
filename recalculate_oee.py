#!/usr/bin/env python3
"""Geçmiş vardiya özetlerini ham kayıtlardan güvenli biçimde yeniden hesapla.

Varsayılan çalışma modu önizlemedir. ``--apply`` verilmedikçe veritabanına
yazılmaz. Tüm uygulanacak kayıtlar tek transaction içinde güncellenir; herhangi
bir hata olursa tamamı geri alınır.
"""

import argparse
import logging
from datetime import date, timedelta


def parse_args():
    parser = argparse.ArgumentParser(
        description="Geçmiş shift_summary OEE kayıtlarını yeniden hesapla",
    )
    parser.add_argument("--date-from", type=date.fromisoformat, required=True)
    parser.add_argument("--date-to", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--line-id",
        type=int,
        action="append",
        dest="line_ids",
        help="Birden fazla kez verilebilir; boşsa tüm aktif hatlar",
    )
    parser.add_argument("--shift", help="Yalnızca bu vardiya kodunu hesapla")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Önizleme yerine sonuçları tek transaction ile kaydet",
    )
    return parser.parse_args()


def _old_summary(db, text, line_id, shift_date, shift_code):
    row = db.execute(text("""
        SELECT total_produced, total_scrap, total_downtime_sec, break_sec,
               oee_availability, oee_performance, oee_quality, oee_overall,
               ideal_cycle_sec, formula_version
        FROM shift_summary
        WHERE line_id=:lid AND shift_date=:d AND shift=:shift
    """), {"lid": line_id, "d": shift_date, "shift": shift_code}).fetchone()
    return dict(row._mapping) if row else None


def main():
    args = parse_args()
    if args.date_to < args.date_from:
        raise SystemExit("--date-to, --date-from değerinden önce olamaz")
    if args.date_to >= date.today():
        raise SystemExit("Bugün veya gelecek tarih hesaplanamaz; en geç dün seçilmeli")
    if (args.date_to - args.date_from).days > 366:
        raise SystemExit("Tek işlemde en fazla 366 gün hesaplanabilir")

    from sqlalchemy import text
    from models.database import DATABASE_URL, SessionLocal
    from services.oee_service import (
        ActiveDowntimeError,
        calculate_shift_summary,
        upsert_shift_summary,
    )
    import shift_utils

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    shift_utils.configure(DATABASE_URL)
    db = SessionLocal()
    calculated = changed = skipped = 0
    try:
        if args.line_ids:
            line_ids = sorted(set(args.line_ids))
        else:
            rows = db.execute(text("""
                SELECT line_id FROM production_lines
                WHERE is_active=TRUE ORDER BY line_id
            """)).fetchall()
            line_ids = [int(row[0]) for row in rows]
        if not line_ids:
            raise SystemExit("Hesaplanacak aktif hat bulunamadı")

        current_date = args.date_from
        while current_date <= args.date_to:
            for line_id in line_ids:
                shifts = shift_utils.get_shifts(
                    line_id=line_id,
                    dt=current_date,
                )
                for shift in shifts:
                    shift_code = shift["code"]
                    if args.shift and shift_code != args.shift:
                        continue
                    try:
                        result = calculate_shift_summary(
                            db,
                            line_id=line_id,
                            shift_date=current_date,
                            shift_code=shift_code,
                            require_complete=True,
                        )
                    except ActiveDowntimeError as exc:
                        print(f"SKIP active_downtime {exc}")
                        skipped += 1
                        continue
                    if result["total_produced"] <= 0:
                        print(
                            f"SKIP no_production line={line_id} "
                            f"date={current_date} shift={shift_code}"
                        )
                        skipped += 1
                        continue

                    old = _old_summary(
                        db,
                        text,
                        line_id,
                        current_date,
                        shift_code,
                    )
                    calculated += 1
                    old_oee = old.get("oee_overall") if old else None
                    is_changed = old is None or any(
                        old.get(key) != result.get(new_key)
                        for key, new_key in (
                            ("total_produced", "total_produced"),
                            ("total_scrap", "total_scrap"),
                            ("total_downtime_sec", "total_downtime_sec"),
                            ("break_sec", "break_sec"),
                            ("ideal_cycle_sec", "ideal_cycle_sec"),
                            ("formula_version", "formula_version"),
                            ("oee_overall", "oee_overall"),
                        )
                    )
                    if is_changed:
                        changed += 1
                    print(
                        f"{'CHANGE' if is_changed else 'SAME'} "
                        f"line={line_id} date={current_date} shift={shift_code} "
                        f"produced={result['total_produced']} "
                        f"scrap={result['total_scrap']} "
                        f"downtime_min={result['total_downtime_sec']/60:.1f} "
                        f"break_min={result['break_sec']/60:.1f} "
                        f"ideal_cycle={result['ideal_cycle_sec']:.3f} "
                        f"theoretical={result['theoretical_output']} "
                        f"oee={old_oee if old_oee is not None else 'NEW'}"
                        f"->{result['oee_overall']}"
                    )
                    if args.apply:
                        upsert_shift_summary(db, result, commit=False)
            current_date += timedelta(days=1)

        if args.apply:
            db.commit()
        else:
            db.rollback()
        mode = "APPLIED" if args.apply else "DRY_RUN"
        print(
            f"RESULT mode={mode} calculated={calculated} "
            f"changed={changed} skipped={skipped}"
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
