"""Opt-in lap geometry reaches submission without changing motion counters."""
import json
import unittest

from running.motion import MotionOptions
from running.route_variation import RouteVariationOptions
from sport_client import SportClient792
import test_motion_defaults as fixtures
from test_loop_route import stadium
from unittest.mock import Mock


class LapSubmissionTests(unittest.TestCase):
    setUp = fixtures.MotionDefaultsTests.setUp
    client = fixtures.MotionDefaultsTests.client

    def test_opt_in_changes_payload_geometry_but_preserves_motion(self):
        client = self.client()
        options = MotionOptions(seed=42)
        client.execute_full_run(2467, 1189, sport_type=6, route_loop=stadium(),
                                motion_options=options)
        original = client.submit_record.call_args.args[0]
        client.execute_full_run(2467, 1189, sport_type=6, route_loop=stadium(),
                                motion_options=options, route_variation=RouteVariationOptions())
        changed = client.submit_record.call_args.args[0]
        self.assertNotEqual(json.loads(original['allLocJson']), json.loads(changed['allLocJson']))
        for key in ('totalSteps', 'speed', 'speedPerTenSec', 'stepsPerTenSec'):
            self.assertEqual(original[key], changed[key])
        self.assertEqual(json.loads(changed['allLocJson'])[-1]['totalDis'], 2467)
        self.assertEqual(changed['speedPerTenSec'][-1]['endTime'] -
                         changed['speedPerTenSec'][-1]['beginTime'], 9000)

    def test_invalid_opt_in_stops_before_any_remote_query(self):
        for kwargs in ({}, {'route_loop': stadium(), 'motion_options': None},
                       {'route_loop': stadium(), 'route_variation': RouteVariationOptions(3)}):
            with self.subTest(kwargs=kwargs):
                client = self.client()
                configured = {'route_variation': RouteVariationOptions(), **kwargs}
                result = client.execute_full_run(2467, 1189, **configured)
                self.assertFalse(result.success)
                client.policy.get_run_policy.assert_not_called()
                client.submit_record.assert_not_called()

    def test_facade_forwards_explicit_options_and_keeps_default_disabled(self):
        client = SportClient792(uid=1, token='DUMMY', unid=2,
                                device_config={'DeviceId': '1' * 32})
        client.running.execute_full_run = Mock()
        client.execute_run()
        self.assertIsNone(client.running.execute_full_run.call_args.kwargs['route_variation'])
        options = RouteVariationOptions(seed=42)
        client.execute_run(route_variation=options)
        self.assertIs(client.running.execute_full_run.call_args.kwargs['route_variation'], options)


if __name__ == '__main__':
    unittest.main()
