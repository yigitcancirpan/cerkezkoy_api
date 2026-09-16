import unittest
from datetime import date, timedelta
from random import Random
from services.auto_planning_service import schedule, capacity_history

OPTIONS=[{'code':'day8','label':'08–16','hours':8,'capacity':80},
         {'code':'day10','label':'08–18','hours':10,'capacity':100},
         {'code':'day12','label':'08–20','hours':12,'capacity':120}]

class AutoPlanningTest(unittest.TestCase):
    def inputs(self, **kw):
        result=dict(start_date='2026-09-07',days=3,finished_stock=0,finished_safety_stock=0,
                    raw_stock_qty=10000,raw_per_piece=1,raw_scrap_pct=0,raw_safety_stock=0,
                    orders={},receipts={},workdays=[0,1,2,3,4],include_planned=False,dispatch_timing='after')
        result.update(kw)
        return result

    def test_no_demand_still_runs_full_base_shift(self):
        r=schedule(self.inputs(),OPTIONS,[])
        self.assertEqual(r['summary']['total_hours'],24)
        self.assertEqual(r['summary']['total_planned'],240)
        self.assertEqual(r['summary']['closing_stock'],240)

    def test_choose_longer_day_only_when_needed(self):
        r=schedule(self.inputs(orders={'2026-09-07':95}),OPTIONS,[])
        self.assertEqual(r['daily'][0]['code'],'day10')
        self.assertEqual(r['daily'][0]['planned_qty'],95)
        self.assertEqual(r['daily'][1]['code'],'day8')
        self.assertEqual(r['daily'][1]['planned_qty'],80)

    def test_future_peak_requires_prebuild(self):
        r=schedule(self.inputs(orders={'2026-09-08':220}),OPTIONS,[])
        self.assertEqual(r['summary']['max_shortage'],0)
        self.assertGreaterEqual(r['daily'][0]['planned_qty'],100)

    def test_morning_dispatch_not_covered_by_same_day_production(self):
        r=schedule(self.inputs(orders={'2026-09-07':90},dispatch_timing='before'),OPTIONS,[])
        self.assertEqual(r['daily'][0]['shortage_qty'],90)
        self.assertEqual(r['daily'][0]['backlog_qty'],0)
        self.assertEqual(r['daily'][0]['status'],'red')

    def test_next_morning_dispatch_prebuilds(self):
        r=schedule(self.inputs(orders={'2026-09-08':90},dispatch_timing='before'),OPTIONS,[])
        self.assertEqual(r['daily'][0]['planned_qty'],90)
        self.assertEqual(r['summary']['max_shortage'],0)

    def test_late_raw_material_does_not_cover_earlier_order(self):
        r=schedule(self.inputs(orders={'2026-09-07':80},raw_stock_qty=0,
            receipts={'2026-09-08':100}),OPTIONS,[])
        self.assertEqual(r['daily'][0]['shortage_qty'],80)
        self.assertEqual(r['daily'][1]['closing_finished_stock'],0)

    def test_raw_reserve_and_fractional_consumption(self):
        r=schedule(self.inputs(orders={'2026-09-07':90},raw_stock_qty=100,raw_per_piece=2,
                   raw_scrap_pct=10,raw_safety_stock=10),OPTIONS,[])
        self.assertEqual(r['daily'][0]['feasible_qty'],40)
        self.assertAlmostEqual(r['daily'][0]['closing_raw_stock'],12)

    def test_locked_day_and_recovery(self):
        lock={'date':'2026-09-07','code':'day10','qty':90}
        r=schedule(self.inputs(orders={'2026-09-08':160}),OPTIONS,[lock])
        self.assertEqual(r['daily'][0]['planned_qty'],90)
        self.assertTrue(r['daily'][0]['locked'])
        self.assertEqual(r['summary']['max_shortage'],0)

    def test_locked_raw_shortfall_preserves_target(self):
        lock={'date':'2026-09-07','code':'day8','qty':80}
        r=schedule(self.inputs(raw_stock_qty=10),OPTIONS,[lock])
        self.assertEqual(r['daily'][0]['planned_qty'],80)
        self.assertEqual(r['daily'][0]['feasible_qty'],10)
        self.assertEqual(r['daily'][0]['raw_limited_qty'],70)

    def test_closed_days_and_empty_calendar(self):
        r=schedule(self.inputs(workdays=[],orders={'2026-09-08':10}),OPTIONS,[])
        self.assertEqual(r['summary']['total_hours'],0)
        self.assertEqual(r['summary']['max_shortage'],10)

    def test_invalid_locks_rejected(self):
        for locks in [[{'date':'2026-09-07','code':'day8','qty':81}],
                      [{'date':'2026-09-09','code':'double','qty':1}],
                      [{'date':'2026-09-10','code':'off','qty':0}],
                      [{'date':'2026-09-07','code':'off','qty':0}]*2]:
            with self.assertRaises(ValueError):schedule(self.inputs(),OPTIONS,locks)

    def test_safety_stock_and_initial_inventory(self):
        r=schedule(self.inputs(finished_stock=100,finished_safety_stock=50,
                              orders={'2026-09-07':80}),OPTIONS,[])
        self.assertEqual(r['daily'][0]['planned_qty'],80)
        self.assertEqual(r['summary']['closing_stock'],260)

    def test_infeasible_capacity_stays_visible(self):
        r=schedule(self.inputs(orders={'2026-09-07':300}),OPTIONS,[])
        self.assertEqual(r['daily'][0]['shortage_qty'],180)
        self.assertEqual(r['summary']['first_shortage_date'],'2026-09-07')

    def test_missing_raw_not_marked_verified(self):
        r=schedule(self.inputs(raw_per_piece=0),OPTIONS,[])
        self.assertTrue(r['warnings'])
        self.assertEqual(r['daily'][0]['status'],'yellow')

    def test_property_stock_conservation_and_maximum_service(self):
        rng=Random(4)
        for _ in range(60):
            orders={str(date(2026,9,7)+timedelta(days=i)):rng.randrange(0,180) for i in range(7)}
            inp=self.inputs(days=7,finished_stock=rng.randrange(50),orders=orders,
                            raw_stock_qty=rng.randrange(500),receipts={'2026-09-10':200})
            r=schedule(inp,OPTIONS,[])
            maximum=schedule(inp,OPTIONS,[],optimize=False)
            for a,m in zip(r['daily'],maximum['daily']):
                self.assertLessEqual(a['shortage_qty'],m['shortage_qty'])
                self.assertLessEqual(a['safety_shortage_qty'],m['safety_shortage_qty'])
                self.assertEqual(a['closing_finished_stock'],a['opening_finished_stock']+a['feasible_qty']-a['order_qty'])
                self.assertGreaterEqual(a['closing_raw_stock'],0)
                self.assertLessEqual(a['planned_qty'],a['capacity'])
                if date.fromisoformat(a['date']).weekday() in inp['workdays']:
                    self.assertGreaterEqual(a['hours'],8)
                    self.assertGreaterEqual(a['planned_qty'],80)

    def test_workday_cannot_be_locked_off_or_below_base(self):
        for code,qty in [('off',0),('day8',0),('day8',79),('day10',50)]:
            with self.assertRaises(ValueError):
                schedule(self.inputs(),OPTIONS,[{'date':'2026-09-07','code':code,'qty':qty}])

    def test_base_option_required_and_positive(self):
        for options in [OPTIONS[1:],[dict(OPTIONS[0],capacity=0)],
                        [OPTIONS[0],dict(OPTIONS[1],capacity=70)]]:
            with self.assertRaises(ValueError):schedule(self.inputs(),options,[])

    def test_seven_selected_days_all_produce(self):
        r=schedule(self.inputs(days=7,workdays=list(range(7))),OPTIONS,[])
        self.assertEqual(r['summary']['total_hours'],56)
        self.assertEqual(r['summary']['total_planned'],560)
        self.assertTrue(all(x['code']=='day8' for x in r['daily']))

    def test_material_shortage_cannot_zero_the_target(self):
        r=schedule(self.inputs(raw_stock_qty=0),OPTIONS,[])
        for d in r['daily']:
            self.assertEqual(d['planned_qty'],80)
            self.assertEqual(d['feasible_qty'],0)
            self.assertEqual(d['raw_limited_qty'],80)
            self.assertEqual(d['code'],'day8')

    def test_high_inventory_does_not_cancel_normal_shift(self):
        r=schedule(self.inputs(finished_stock=10000,orders={'2026-09-07':10}),OPTIONS,[])
        self.assertEqual(r['daily'][0]['planned_qty'],80)
        self.assertEqual(r['summary']['closing_stock'],10230)

    def test_balanced_week_replaces_three_long_days_at_equal_hours(self):
        dates = [f'2026-09-{day:02d}' for day in range(14, 20)]
        inputs = self.inputs(
            start_date=dates[0], days=6, finished_stock=5700,
            finished_safety_stock=2940, raw_stock_qty=100000,
            workdays=list(range(6)),
            orders=dict(zip(dates, [4860, 3780, 3600, 5040, 4140, 2880])),
        )
        options = [
            {'code':'day8','label':'08–16','hours':8,'capacity':2940},
            {'code':'day10','label':'08–18','hours':10,'capacity':3660},
            {'code':'day12','label':'08–20','hours':12,'capacity':4411},
        ]
        flexible = schedule(inputs, options, [], mode='daily_flexible')
        balanced = schedule(inputs, options, [], mode='balanced_weekly')
        self.assertEqual(flexible['summary']['total_hours'], 60)
        self.assertEqual(balanced['summary']['total_hours'], 60)
        self.assertEqual(flexible['comparison']['daily_flexible']['long_day_count'], 3)
        self.assertEqual(balanced['comparison']['balanced_weekly']['long_day_count'], 1)
        self.assertEqual(balanced['summary']['total_planned'], 21600)
        self.assertEqual(balanced['summary']['closing_stock'], 3000)
        self.assertGreaterEqual(min(x['closing_finished_stock'] for x in balanced['daily']), 2940)

    def test_balanced_mode_preserves_locked_day(self):
        lock = {'date':'2026-09-08','code':'day12','qty':110}
        r=schedule(self.inputs(days=7), OPTIONS, [lock], mode='balanced_weekly')
        row=next(x for x in r['daily'] if x['date']==lock['date'])
        self.assertEqual((row['code'],row['planned_qty'],row['locked']),('day12',110,True))

    def test_daily_mode_still_available_for_comparison(self):
        r=schedule(self.inputs(), OPTIONS, [], mode='daily_flexible')
        self.assertEqual(r['planning_mode'],'daily_flexible')
        self.assertEqual(r['comparison']['selected'],'daily_flexible')

    def test_unknown_planning_mode_rejected(self):
        with self.assertRaises(ValueError):
            schedule(self.inputs(), OPTIONS, [], mode='financial_magic')

    def test_break_work_is_used_only_when_base_capacity_is_not_enough(self):
        options = [
            {'code':'day8','label':'08–16','hours':8,'capacity':80,
             'break_extra_capacity':10,'break_minutes':60},
            {'code':'day10','label':'08–18','hours':10,'capacity':100},
        ]
        normal = schedule(self.inputs(orders={'2026-09-07':80}), options, [])
        needed = schedule(self.inputs(orders={'2026-09-07':90}), options, [])
        self.assertFalse(normal['daily'][0]['break_work'])
        self.assertEqual(normal['daily'][0]['break_extra_qty'], 0)
        self.assertTrue(needed['daily'][0]['break_work'])
        self.assertEqual(needed['daily'][0]['code'], 'day8')
        self.assertEqual(needed['daily'][0]['break_extra_qty'], 10)
        self.assertEqual(needed['daily'][0]['break_minutes'], 60)
        self.assertEqual(needed['comparison']['balanced_weekly']['break_work_day_count'], 1)

    def test_break_lock_and_pair_validation(self):
        options = [dict(OPTIONS[0],break_extra_capacity=10,break_minutes=60)]
        lock = {'date':'2026-09-07','code':'day8','qty':90,'break_work':True}
        result = schedule(self.inputs(), options, [lock])
        self.assertTrue(result['daily'][0]['break_work'])
        self.assertTrue(result['daily'][0]['locked'])
        for bad in [dict(OPTIONS[0],break_extra_capacity=10),
                    dict(OPTIONS[0],break_minutes=60)]:
            with self.assertRaises(ValueError):
                schedule(self.inputs(), [bad], [])

    def test_history_separates_shift_duration_base_and_break_output(self):
        rows = []
        for index, good in enumerate(range(100, 121)):
            rows.append(dict(
                shift_date=date(2026,8,1)+timedelta(days=index), model_id=1,
                total_good=good, total_produced=good+5, planned_base_sec=8*3600,
                ideal_cycle_detail=[{'model_id':1,'produced':good+5}],
                break_good=10, break_minutes=60,
            ))
        for index, good in enumerate([180,190,200,210,220,230]):
            rows.append(dict(
                shift_date=date(2026,8,1)+timedelta(days=index), model_id=1,
                total_good=good, total_produced=good, planned_base_sec=10*3600,
                ideal_cycle_detail=[{'model_id':1,'produced':good}],
                break_good=20, break_minutes=60,
            ))
        history = capacity_history(rows, 1)
        day8 = history['profiles']['day8']
        day10 = history['profiles']['day10']
        self.assertEqual(day8['sample_count'], 20)
        self.assertEqual(day8['technical_base_capacity'], 108)
        self.assertEqual(day8['technical_break_extra'], 10)
        self.assertEqual(day8['normal_base_capacity'], 106)
        self.assertEqual(day10['normal_base_capacity'], 185)
        self.assertEqual(day10['normal_break_extra'], 20)
        self.assertEqual(day10['break_minutes'], 60)

    def test_history_excludes_mixed_zero_and_invalid_time(self):
        def row(good,seconds,models):
            return dict(shift_date=date(2026,9,1),model_id=1,total_good=good,total_produced=100,
                        planned_base_sec=seconds,ideal_cycle_detail=[{'model_id':m,'produced':50} for m in models])
        r=capacity_history([row(80,8*3600,[1]),row(90,10*3600,[1]),row(0,8*3600,[1]),
                            row(90,8*3600,[1,2]),row(80,0,[1]),row(101,3600,[1])],1)
        self.assertEqual(r['sample_count'],2)
        self.assertEqual(r['excluded_count'],4)
        self.assertEqual(r['gross_good_per_hour'],9.44)
        self.assertEqual(r['cautious_good_per_hour'],9)

if __name__=='__main__':unittest.main()
