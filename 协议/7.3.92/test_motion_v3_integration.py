"""Offline V3 integration: final geometry, payload, defaults and failure boundaries."""
import json
import socket
import unittest
from unittest.mock import patch
from dataclasses import replace

from running.builder import RunningTrackBuilder
from running.motion_v3 import MotionV3Options, build_timeline
from running.route_variation import RouteVariationOptions
from test_loop_route import stadium
import test_motion_defaults as defaults


class V3IntegrationTests(unittest.TestCase):
    setUp = defaults.MotionDefaultsTests.setUp
    client = defaults.MotionDefaultsTests.client
    cli = defaults.MotionDefaultsTests.cli
    def test_opt_in_client_builds_from_prepared_geometry(self):
        client = self.client()
        result = client.execute_full_run(2537,1183,sport_type=6,
                                         motion_options=MotionV3Options(seed=7))
        self.assertFalse(result.success)  # mocked settlement deliberately stops here
        client.submit_record.assert_called_once()
        payload = client.submit_record.call_args.args[0]
        native = json.loads(payload['allLocJson'])
        self.assertEqual(native[0]['speed'], 0)
        self.assertEqual(native[-1]['speed'], 0)
        self.assertEqual(native[-1]['steps'], payload['totalSteps'])
        self.assertEqual(sum(x['stepsNum'] for x in payload['stepsPerTenSec']), payload['totalSteps'])

    def test_final_geometry_is_shared_with_motion_and_lap_variation(self):
        loop = stadium()
        checkpoints = [{'lat':loop[i][0],'lng':loop[i][1]}
                       for i in (0,33,16,49,8)]
        for varied in (False,True):
            builder = RunningTrackBuilder(0,0,1)
            geometry = builder.prepare_route(*loop[0],checkpoints,2537,route_loop=loop,
                route_variation=RouteVariationOptions() if varied else None,seed=7)
            t = build_timeline(geometry.motion_route,1183,MotionV3Options(seed=7))
            points,fixed = builder.generate_track_around_points(*loop[0],checkpoints,2537,1183,
                route_loop=loop,timeline=t,prepared_route=geometry)
            payload,_,_ = builder.build_submission_payload(2537,1183,t.total_steps,0,1183000,
                                                          points,fixed,timeline=t)
            self.assertEqual(payload['totalSteps'],t.total_steps)
            self.assertIs(builder.route_geometry,geometry.variation)
            self.assertAlmostEqual(geometry.motion_route.length_m,2537,delta=1e-7)
            for ms in (0,12345,600123,1183000):
                d,_=t.counters_at(ms)
                self.assertTrue(0 <= d <= 2537+1e-7)

    def test_infeasible_model_stops_before_submission(self):
        client = self.client()
        result=client.execute_full_run(2537,10,sport_type=6,
                                      motion_options=MotionV3Options(seed=7))
        self.assertFalse(result.success)
        client.submit_record.assert_not_called()

    def test_v3_cli_and_facade_forward_options(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        import io
        cli=self.cli()
        client=Mock()
        client.execute_run.return_value=SimpleNamespace(success=False,error_code=-1,message='offline')
        with patch.object(cli,'SportClient792',return_value=client), patch('sys.stdout',new_callable=io.StringIO), \
                patch('sys.argv',['cli.py','--run','--motion-model','v3','--seed','7']):
            cli.main()
        self.assertEqual(client.execute_run.call_args.kwargs['motion_options'],MotionV3Options(seed=7))

    def test_generated_seed_and_invalid_options(self):
        client = self.client()
        with patch('running.client.random.SystemRandom.randrange', return_value=42):
            result = client.execute_full_run(2537,1183,sport_type=6,
                                            motion_options=MotionV3Options())
        self.assertFalse(result.success)
        client.submit_record.assert_called_once()
        client = self.client()
        result = client.execute_full_run(2537,1183,sport_type=6,
                                        motion_options=MotionV3Options(seed=-1))
        self.assertFalse(result.success)
        client.submit_record.assert_not_called()

    def test_bend_postcheck_stops_before_submission(self):
        client=self.client()
        with patch('running.motion_v3.MotionRoute.validate_bends',side_effect=ValueError('弯道求解后校验失败')):
            result=client.execute_full_run(2000,800,sport_type=6,
                                          motion_options=MotionV3Options(seed=7))
        self.assertFalse(result.success)
        self.assertIn('弯道',result.message)
        client.submit_record.assert_not_called()


if __name__ == '__main__':
    unittest.main()
