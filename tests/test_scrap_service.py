import unittest
from datetime import date, timedelta
from unittest.mock import patch

from services.scrap_service import (
    ScrapDateError,
    ScrapShiftError,
    historical_shift_options,
    recalculate_existing_summary,
    resolve_shift_code,
    summary_response,
    validate_production_date,
)


class _Result:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return self.value

    def fetchall(self):
        return self.value


class _Db:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((str(statement), params))
        return _Result(self.responses.pop(0))


class ScrapServiceTest(unittest.TestCase):
    def test_today_and_seven_days_back_are_allowed(self):
        today = date(2026, 8, 24)
        self.assertEqual(
            validate_production_date(today, today=today),
            today,
        )
        oldest = today - timedelta(days=7)
        self.assertEqual(
            validate_production_date(oldest, today=today),
            oldest,
        )

    def test_future_and_eight_days_back_are_rejected(self):
        today = date(2026, 8, 24)
        with self.assertRaises(ScrapDateError):
            validate_production_date(today + timedelta(days=1), today=today)
        with self.assertRaises(ScrapDateError):
            validate_production_date(today - timedelta(days=8), today=today)

    @patch("shift_utils.get_shifts")
    def test_single_shift_is_inferred_and_requested_shift_is_validated(self, get_shifts):
        get_shifts.return_value = [
            {"code": "vardiya_1", "label": "1. Vardiya"},
        ]
        code, shifts = resolve_shift_code(
            line_id=2,
            production_date=date(2026, 8, 22),
            requested_shift=None,
        )
        self.assertEqual(code, "vardiya_1")
        self.assertEqual(len(shifts), 1)
        with self.assertRaises(ScrapShiftError):
            resolve_shift_code(
                line_id=2,
                production_date=date(2026, 8, 22),
                requested_shift="vardiya_2",
            )

    @patch("shift_utils.get_shifts")
    def test_multiple_shifts_require_an_explicit_choice(self, get_shifts):
        get_shifts.return_value = [
            {"code": "vardiya_1", "label": "1. Vardiya"},
            {"code": "vardiya_2", "label": "2. Vardiya"},
        ]
        with self.assertRaises(ScrapShiftError):
            resolve_shift_code(
                line_id=2,
                production_date=date(2026, 8, 22),
                requested_shift=None,
            )

    def test_existing_summary_updates_only_quality_dependent_fields(self):
        db = _Db(
            (2104, 54.3, 75.5, "oee-v3"),
            (4,),
            None,
        )

        result = recalculate_existing_summary(
            db,
            line_id=2,
            production_date=date(2026, 8, 22),
            shift="vardiya_1",
        )

        self.assertEqual(result["total_scrap"], 4)
        self.assertEqual(result["total_good"], 2100)
        self.assertEqual(result["oee_quality"], 99.8)
        self.assertEqual(result["oee_overall"], 40.9)
        update_sql = db.calls[2][0]
        self.assertIn("SET total_scrap", update_sql)
        self.assertNotIn("oee_availability=", update_sql)
        self.assertNotIn("oee_performance=", update_sql)
        self.assertNotIn("planned_base_sec=", update_sql)
        response = summary_response(result)
        self.assertTrue(response["summary_recalculated"])
        self.assertEqual(response["summary"]["total_scrap"], 4)

    def test_missing_summary_is_not_created(self):
        db = _Db(None)
        result = recalculate_existing_summary(
            db,
            line_id=2,
            production_date=date(2026, 8, 22),
            shift="vardiya_1",
        )
        self.assertIsNone(result)
        self.assertEqual(len(db.calls), 1)

    def test_scrap_cannot_exceed_total_production(self):
        db = _Db(
            (10, 80.0, 90.0, "oee-v3"),
            (11,),
        )
        with self.assertRaisesRegex(ValueError, "üretimden büyük"):
            recalculate_existing_summary(
                db,
                line_id=2,
                production_date=date(2026, 8, 22),
                shift="vardiya_1",
            )
        self.assertEqual(len(db.calls), 2)

    def test_historical_options_use_saved_summary_duration(self):
        db = _Db([("vardiya_1", 36000)])
        options = historical_shift_options(
            db,
            line_id=2,
            production_date=date(2026, 8, 18),
            resolved_shifts=[{
                "code": "vardiya_1",
                "label": "1. Vardiya",
                "start_hour": 8,
                "end_hour": 20,
            }],
        )
        self.assertEqual(options[0]["start_hour"], 8)
        self.assertEqual(options[0]["end_hour"], 18)
        self.assertEqual(options[0]["source"], "saved_summary")


if __name__ == "__main__":
    unittest.main()
