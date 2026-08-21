import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch


def _install_dependency_stubs():
    if "psycopg2" not in sys.modules:
        psycopg2_stub = types.ModuleType("psycopg2")
        psycopg2_stub.connect = lambda *_args, **_kwargs: None
        extras_stub = types.ModuleType("psycopg2.extras")
        extras_stub.RealDictCursor = object
        sys.modules["psycopg2"] = psycopg2_stub
        sys.modules["psycopg2.extras"] = extras_stub

    if "paho.mqtt.client" not in sys.modules:
        class FakeClient:
            def __init__(self, *_args, **_kwargs):
                pass

        paho = types.ModuleType("paho")
        mqtt_pkg = types.ModuleType("paho.mqtt")
        mqtt_client = types.ModuleType("paho.mqtt.client")
        mqtt_client.Client = FakeClient
        mqtt_client.MQTTv311 = object()
        paho.mqtt = mqtt_pkg
        mqtt_pkg.client = mqtt_client
        sys.modules["paho"] = paho
        sys.modules["paho.mqtt"] = mqtt_pkg
        sys.modules["paho.mqtt.client"] = mqtt_client

    if "requests" not in sys.modules:
        requests_stub = types.ModuleType("requests")
        requests_stub.Session = lambda: Mock()
        sys.modules["requests"] = requests_stub


_install_dependency_stubs()

from services.downtime_monitor import DowntimeMonitor, LineState


class DowntimeShiftEndTest(unittest.TestCase):
    def setUp(self):
        self.monitor = DowntimeMonitor(
            lines_config=[{
                "line_id": 2,
                "line_name": "World Hattı",
                "mqtt_prefix": "fabrika/hat2",
            }]
        )
        self.state: LineState = self.monitor.lines["2"]
        self.state.active_downtime_id = 700
        self.state.active_reason_code = "BELIRLENMEDI"
        self.state.active_shift = "vardiya_1"
        self.state.active_started_at = datetime(
            2026, 8, 22, 15, 30, tzinfo=timezone(timedelta(hours=3))
        )

    def test_shift_end_closes_without_changing_reason(self):
        end_at = datetime(2026, 8, 22, 16, 0, tzinfo=timezone(timedelta(hours=3)))
        self.monitor._sync = Mock(return_value=True)
        self.monitor._resolved_shift_end = Mock(return_value=end_at)
        self.monitor._auto_stop = Mock()

        with patch("services.downtime_monitor.datetime") as datetime_mock:
            datetime_mock.now.return_value = end_at
            self.monitor._shift_end_done(self.state, 700, end_at)

        self.monitor._auto_stop.assert_called_once()
        _state, note = self.monitor._auto_stop.call_args.args
        self.assertIs(_state, self.state)
        self.assertIn("sebep korunarak", note)
        self.assertEqual(self.monitor._auto_stop.call_args.kwargs["ended_at"], end_at)
        self.assertEqual(self.state.active_reason_code, "BELIRLENMEDI")

    def test_admin_extension_reschedules_instead_of_closing(self):
        old_end = datetime(2026, 8, 22, 16, 0, tzinfo=timezone(timedelta(hours=3)))
        new_end = datetime(2026, 8, 22, 18, 0, tzinfo=timezone(timedelta(hours=3)))
        self.monitor._sync = Mock(return_value=True)
        self.monitor._resolved_shift_end = Mock(return_value=new_end)
        self.monitor._schedule_shift_end = Mock()
        self.monitor._auto_stop = Mock()

        with patch("services.downtime_monitor.datetime") as datetime_mock:
            datetime_mock.now.return_value = old_end
            self.monitor._shift_end_done(self.state, 700, old_end)

        self.monitor._schedule_shift_end.assert_called_once_with(self.state)
        self.monitor._auto_stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
