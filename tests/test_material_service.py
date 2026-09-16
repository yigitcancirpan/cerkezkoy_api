import unittest

from services.material_service import unique_models, split_production
from services.auto_planning_service import schedule


class MaterialAccountingTest(unittest.TestCase):
    def setUp(self):
        self.models = unique_models([
            {'material_id': 4, 'model_id': 4},
            {'material_id': 5, 'model_id': 5},
        ])

    def test_mixed_shift_is_not_assigned_to_last_model(self):
        parts = split_production({'total_produced': 350, 'model_id': 5,
            'ideal_cycle_detail': [{'model_id': 4, 'produced': 100},
                                   {'model_id': 5, 'produced': 200}]}, self.models)
        self.assertEqual(parts, {4: 100, 5: 200, None: 50})
        self.assertEqual(sum(parts.values()), 350)

    def test_inconsistent_detail_does_not_create_production(self):
        self.assertEqual(split_production({'total_produced': 10,
            'ideal_cycle_detail': [{'model_id': 4, 'produced': 20}]}, self.models), {None: 10})

    def test_ambiguous_or_zero_models_are_not_guessed(self):
        models = unique_models([{'material_id': 4, 'model_id': 4},
                                {'material_id': 5, 'model_id': 4},
                                {'material_id': 6, 'model_id': 0}])
        self.assertEqual(models, {})
        self.assertEqual(split_production({'total_produced': 12}, models), {None: 12})

    def test_stock_floor_and_deficit_remain_visible(self):
        inputs = dict(start_date='2026-09-14', days=2, finished_stock=0,
                      finished_safety_stock=100, raw_stock_qty=0, raw_per_piece=1,
                      raw_scrap_pct=0, raw_safety_stock=0, orders={}, receipts={},
                      workdays=[0, 1], include_planned=False, dispatch_timing='after')
        result = schedule(inputs, [{'code': 'day8', 'label': '08–16', 'hours': 8, 'capacity': 80}], [])
        for day in result['daily']:
            self.assertEqual(day['minimum_closing_stock'], 100)
            self.assertEqual(day['stock_margin'], -100)


if __name__ == '__main__':
    unittest.main()
