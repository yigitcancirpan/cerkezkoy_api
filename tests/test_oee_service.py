import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from services.oee_service import (
    aggregate_oee,
    calculate_factors,
    downtime_buckets,
    merge_intervals,
)
from services.break_service import split_interval_by_breaks


TZ = ZoneInfo("Europe/Istanbul")


class OeeFormulaTest(unittest.TestCase):
    def test_oee_components_follow_standard_formula(self):
        result = calculate_factors(
            planned_base_sec=36000,
            excluded_sec=3600,
            unplanned_sec=1800,
            produced=3000,
            scrap=30,
            ideal_cycle_sec=9.0,
        )
        self.assertEqual(result["planned_production_sec"], 32400)
        self.assertEqual(result["run_time_sec"], 30600)
        self.assertEqual(result["good"], 2970)
        self.assertEqual(result["availability"], 94.4)
        self.assertEqual(result["performance"], 88.2)
        self.assertEqual(result["quality"], 99.0)
        self.assertEqual(result["overall"], 82.5)

    def test_performance_is_capped_at_one_hundred(self):
        result = calculate_factors(
            planned_base_sec=3600,
            excluded_sec=0,
            unplanned_sec=0,
            produced=1000,
            scrap=0,
            ideal_cycle_sec=9.0,
        )
        self.assertEqual(result["performance"], 100.0)
        self.assertEqual(result["overall"], 100.0)

    def test_overlapping_intervals_are_not_double_counted(self):
        intervals = merge_intervals([
            (self._dt(10, 0), self._dt(10, 15)),
            (self._dt(10, 10), self._dt(10, 20)),
            (self._dt(11, 0), self._dt(11, 5)),
        ])
        self.assertEqual(intervals, [
            (self._dt(10, 0), self._dt(10, 20)),
            (self._dt(11, 0), self._dt(11, 5)),
        ])

    def test_planned_break_takes_priority_over_overlapping_stop(self):
        events = [
            {
                "started_at": self._dt(10, 0),
                "ended_at": self._dt(10, 15),
                "reason_code": "OPERATOR",
                "exclude_from_oee": True,
            },
            {
                "started_at": self._dt(10, 10),
                "ended_at": self._dt(10, 20),
                "reason_code": "BELIRLENMEDI",
                "exclude_from_oee": False,
            },
        ]
        excluded, unplanned = downtime_buckets(
            events,
            self._dt(8, 0),
            self._dt(18, 0),
            break_windows=[(self._dt(10, 0), self._dt(10, 15))],
        )
        self.assertEqual(excluded, 15 * 60)
        self.assertEqual(unplanned, 5 * 60)

    def test_scheduled_break_without_real_stop_excludes_nothing(self):
        excluded, unplanned = downtime_buckets(
            [],
            self._dt(8, 0),
            self._dt(18, 0),
            break_windows=[(self._dt(10, 0), self._dt(10, 15))],
        )
        self.assertEqual((excluded, unplanned), (0, 0))

    def test_operator_reason_outside_schedule_is_unplanned(self):
        excluded, unplanned = downtime_buckets(
            [{
                "started_at": self._dt(10, 15),
                "ended_at": self._dt(10, 20),
                "reason_code": "OPERATOR",
                "exclude_from_oee": True,
            }],
            self._dt(8, 0),
            self._dt(18, 0),
            break_windows=[(self._dt(10, 0), self._dt(10, 15))],
        )
        self.assertEqual((excluded, unplanned), (0, 5 * 60))

    def test_stop_spanning_break_is_split_five_fifteen_five(self):
        segments = split_interval_by_breaks(
            self._dt(9, 55),
            self._dt(10, 20),
            [(self._dt(10, 0), self._dt(10, 15))],
        )
        self.assertEqual(
            [
                (int((item["end"]-item["start"]).total_seconds()), item["is_break"])
                for item in segments
            ],
            [(300, False), (900, True), (300, False)],
        )

    def test_mixed_models_use_total_ideal_time(self):
        result = calculate_factors(
            planned_base_sec=3600,
            excluded_sec=0,
            unplanned_sec=0,
            produced=500,
            scrap=0,
            ideal_cycle_sec=7.44,
            ideal_time_sec=300*6.4 + 200*9.0,
        )
        self.assertEqual(result["ideal_time_sec"], 3720.0)
        self.assertEqual(result["performance"], 100.0)

    def test_aggregate_uses_time_and_piece_weights(self):
        result = aggregate_oee([
            {
                "planned_production_sec": 100,
                "run_time_sec": 80,
                "ideal_time_sec": 64,
                "total_produced": 100,
                "total_scrap": 10,
                "total_good": 90,
            },
            {
                "planned_production_sec": 300,
                "run_time_sec": 270,
                "ideal_time_sec": 216,
                "total_produced": 300,
                "total_scrap": 0,
                "total_good": 300,
            },
        ])
        self.assertEqual(result["availability"], 87.5)
        self.assertEqual(result["performance"], 80.0)
        self.assertEqual(result["quality"], 97.5)
        self.assertEqual(result["overall"], 68.2)

    @staticmethod
    def _dt(hour, minute):
        return datetime(2026, 8, 21, hour, minute, tzinfo=TZ)


if __name__ == "__main__":
    unittest.main()
