import sys
import types
import unittest
from datetime import date, datetime, timedelta, timezone


# CI/scratch ortamında psycopg2 bulunmayabilir. Bu testler DB'ye bağlanmaz;
# yalnızca shift_utils'in saf tarih/saat ve öncelik kurallarını doğrular.
try:
    import psycopg2  # noqa: F401
except ModuleNotFoundError:
    psycopg2_stub = types.ModuleType("psycopg2")
    psycopg2_stub.connect = lambda *_args, **_kwargs: None
    extras_stub = types.ModuleType("psycopg2.extras")
    extras_stub.RealDictCursor = object
    sys.modules["psycopg2"] = psycopg2_stub
    sys.modules["psycopg2.extras"] = extras_stub

import shift_utils


class ShiftTimeRulesTest(unittest.TestCase):
    def setUp(self):
        self.old_state = {
            "ts": shift_utils._state["ts"],
            "shifts": shift_utils._state["shifts"],
            "excluded": shift_utils._state["excluded"],
            "overrides": shift_utils._state["overrides"],
        }
        shift_utils._state.update({
            "ts": shift_utils.time.time(),
            "excluded": set(),
            "shifts": [
                {
                    "code": "vardiya_1", "label": "Genel", "start_hour": 8,
                    "end_hour": 18, "latest_end": 18, "window_hours": 10,
                    "planned_seconds": 36000, "display_order": 1,
                    "is_active": True, "line_id": None, "day_of_week": None,
                },
                {
                    "code": "vardiya_1", "label": "Cumartesi", "start_hour": 8,
                    "end_hour": 16, "latest_end": 17, "window_hours": 8,
                    "planned_seconds": 28800, "display_order": 1,
                    "is_active": True, "line_id": None, "day_of_week": 6,
                },
            ],
            "overrides": [],
        })

    def tearDown(self):
        shift_utils._state.update(self.old_state)

    def test_saturday_rule_overrides_general_rule(self):
        saturday = date(2026, 8, 22)
        shift = shift_utils.get_shifts(dt=saturday)[0]
        self.assertEqual(shift["label"], "Cumartesi")
        end_at = shift_utils.shift_end_datetime(shift, saturday, timezone(timedelta(hours=3)))
        self.assertEqual(end_at.isoformat(), "2026-08-22T16:00:00+03:00")

    def test_admin_change_to_18_changes_exact_end_without_code_change(self):
        saturday = date(2026, 8, 22)
        saturday_row = shift_utils._state["shifts"][1]
        saturday_row.update({"end_hour": 18, "window_hours": 10, "planned_seconds": 36000})
        shift = shift_utils.shift_by_code("vardiya_1", dt=saturday)
        end_at = shift_utils.shift_end_datetime(shift, saturday, timezone(timedelta(hours=3)))
        self.assertEqual(end_at.hour, 18)

    def test_date_override_has_highest_priority(self):
        saturday = date(2026, 8, 22)
        shift_utils._state["overrides"] = [{
            "override_date": saturday, "code": "vardiya_1", "line_id": None,
            "label": "Uzun Cumartesi", "start_hour": 8, "end_hour": 18,
            "latest_end": 19, "window_hours": 10, "planned_seconds": 36000,
        }]
        shift = shift_utils.get_shifts(dt=saturday)[0]
        self.assertEqual(shift["label"], "Uzun Cumartesi")
        self.assertEqual(shift["end_hour"], 18)

    def test_overnight_shift_uses_previous_shift_date(self):
        shift = {"start_hour": 22, "end_hour": 6}
        observed = datetime(2026, 8, 23, 2, 0, tzinfo=timezone.utc)
        shift_date = shift_utils.shift_date_for_datetime(shift, observed)
        end_at = shift_utils.shift_end_datetime(shift, shift_date, timezone.utc)
        self.assertEqual(shift_date, date(2026, 8, 22))
        self.assertEqual(end_at, datetime(2026, 8, 23, 6, 0, tzinfo=timezone.utc))

    def test_latest_end_24_is_next_midnight(self):
        shift = {"start_hour": 8, "latest_end": 24}
        before = datetime(2026, 8, 22, 23, 59, tzinfo=timezone.utc)
        after = datetime(2026, 8, 23, 0, 1, tzinfo=timezone.utc)
        self.assertEqual(shift_utils.resolve_shift_date(shift, before), date(2026, 8, 21))
        self.assertEqual(shift_utils.resolve_shift_date(shift, after), date(2026, 8, 22))


if __name__ == "__main__":
    unittest.main()
