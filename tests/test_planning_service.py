import unittest
from datetime import date

from services.planning_service import build_forecast


class PlanningForecastTest(unittest.TestCase):
    def forecast(self, **overrides):
        params = {
            "start_date": date(2026, 8, 31),
            "end_date": date(2026, 9, 2),
            "opening_finished_stock": 100,
            "opening_raw_stock": 0,
            "raw_per_piece": 0,
            "raw_scrap_pct": 0,
            "finished_safety_stock": 0,
            "raw_safety_stock": 0,
            "order_by_date": {},
            "plan_by_date": {},
            "receipt_by_date": {},
        }
        params.update(overrides)
        return build_forecast(**params)

    def test_same_day_production_can_cover_same_day_order(self):
        result = self.forecast(
            order_by_date={date(2026, 8, 31): 150},
            plan_by_date={date(2026, 8, 31): {"qty": 75, "source": "weekly_default"}},
        )
        first = result["daily"][0]
        self.assertEqual(first["closing_finished_stock"], 25)
        self.assertEqual(first["shortage_qty"], 0)

    def test_backlog_carries_across_week_and_month(self):
        result = self.forecast(
            order_by_date={date(2026, 8, 31): 250},
            plan_by_date={date(2026, 9, 1): {"qty": 40, "source": "daily_override"}},
        )
        self.assertEqual(result["daily"][0]["closing_finished_stock"], -150)
        self.assertEqual(result["daily"][1]["opening_finished_stock"], -150)
        self.assertEqual(result["daily"][1]["closing_finished_stock"], -110)
        self.assertEqual(result["weekly"][0]["max_shortage_qty"], 150)
        self.assertEqual(result["monthly"][0]["closing_finished_stock"], -150)
        self.assertEqual(result["monthly"][1]["opening_finished_stock"], -150)

    def test_raw_material_caps_feasible_production(self):
        result = self.forecast(
            opening_raw_stock=100,
            raw_per_piece=2,
            raw_scrap_pct=10,
            plan_by_date={date(2026, 8, 31): {"qty": 100, "source": "target_and_shifts"}},
        )
        first = result["daily"][0]
        self.assertEqual(first["feasible_qty"], 45)
        self.assertAlmostEqual(first["raw_consumed_qty"], 99.0)
        self.assertAlmostEqual(first["raw_shortage_qty"], 120.0)
        self.assertEqual(first["status"], "orange")

    def test_raw_receipt_is_available_before_same_day_production(self):
        result = self.forecast(
            opening_raw_stock=0,
            raw_per_piece=1,
            plan_by_date={date(2026, 8, 31): {"qty": 80, "source": "daily_override"}},
            receipt_by_date={date(2026, 8, 31): 80},
        )
        first = result["daily"][0]
        self.assertEqual(first["feasible_qty"], 80)
        self.assertEqual(first["closing_raw_stock"], 0)

    def test_period_keeps_first_and_max_shortage_even_if_recovered(self):
        result = self.forecast(
            order_by_date={date(2026, 8, 31): 180},
            plan_by_date={date(2026, 9, 1): {"qty": 100, "source": "daily_override"}},
        )
        week = result["weekly"][0]
        self.assertEqual(week["first_shortage_date"], "2026-08-31")
        self.assertEqual(week["max_shortage_qty"], 80)
        self.assertEqual(week["closing_finished_stock"], 20)


if __name__ == "__main__":
    unittest.main()
