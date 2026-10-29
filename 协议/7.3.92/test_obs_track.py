"""Offline tests for the Android map payload and record/OBS association."""
import base64
import gzip
import importlib.util
import io
import json
import math
from pathlib import Path
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from running.builder import RunningTrackBuilder
from running.client import RunningClient
from running.models import GpsPoint
from running.obs import map_points


def decode(value):
    return gzip.decompress(base64.b64decode(value)).decode('utf-8')


def built_payload():
    builder = RunningTrackBuilder(1, 2, 1, uuid_str='offline-track')
    points, fixed = builder.generate_track_around_points(30, 120, total_distance_m=2150,
                                                       total_time_sec=760, start_time_ms=1700000000000)
    return builder.build_submission_payload(2150, 760, 2921, 1700000000000,
                                            1700000760000, points, fixed)


class ObsTrackTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        guard.start()
        self.addCleanup(guard.stop)

    def client(self, submit=None):
        client = RunningClient(uid=1, token='DUMMY', unid=2, device_config={'DeviceId': '1'*32})
        client.profile.get_server_time = Mock(return_value=SimpleNamespace(
            server_timestamp_ms=1700000760000, offset_seconds=0))
        client.telemetry.send_startup_pv = Mock()
        client.telemetry.send_page_exposure = Mock()
        client.policy.get_run_policy = Mock(return_value=SimpleNamespace(
            min_distance=2000, raw={'runRuleModel': {'minRunTime': 0}, 'policy': 1}))
        client.policy.get_geo_fence = Mock(return_value=[])
        client.submit_record = Mock(return_value=submit or {'error': 10000, 'data': {'rrid': 1345698889}})
        client.request_obs_upload_url = Mock(return_value={
            'error': 10000, 'data': {'signedUrl': 'https://obs.invalid/object'}})
        client.upload_to_obs = Mock(return_value={'error': 10000})
        client.verify_obs_object = Mock(return_value={'error': 10000})
        client.history.get_one_record = Mock(return_value=None)
        client.get_rundetail_config = Mock()
        return client

    def test_run_data_is_android_wrapper_with_map_coordinates(self):
        body, payload, _ = built_payload()
        wrapper = json.loads(decode(payload['run_data']))
        self.assertIsInstance(wrapper, dict)
        self.assertIs(wrapper['useZip'], False)
        self.assertEqual(wrapper['allLocJson'], body['allLocJson'])
        points = json.loads(wrapper['allLocJson'])
        self.assertGreater(len(points), 2)
        self.assertTrue(all(p['coorType'] == 'gcj02' and 'gLat' in p and 'gLng' in p for p in points))
        self.assertEqual(points[0]['totalTime'], 0)
        self.assertEqual(points[-1]['totalTime'], 760)
        self.assertEqual(points[-1]['totalDis'], 2150)
        self.assertEqual(points[-1]['steps'], 2921)

    def test_bd09_conversion_matches_independent_reference_vector(self):
        from running.obs import bd09_to_gcj02
        lat, lng = bd09_to_gcj02(38.901678, 121.540241)
        self.assertAlmostEqual(lat, 38.8956025774013, places=9)
        self.assertAlmostEqual(lng, 121.5337497718317, places=9)

    def test_map_point_pace_uses_minutes_per_km_not_meters_per_second(self):
        # Phone regression: 1.933355844 m/s was rendered as "fastest 1:56".
        # Its actual pace is about 8:37; 2 m/s is 8:20 and 4 m/s is 4:10.
        speeds = (1.9333558443166179, 2.0, 4.0)
        points = [GpsPoint(30 + i * .0001, 120, i * 10000, speed=speed)
                  for i, speed in enumerate(speeds)]
        native = map_points(points, 0, 20, 60, 40)
        self.assertAlmostEqual(native[0]['speed'] * 60, 517.235, delta=.01)
        self.assertAlmostEqual(native[1]['speed'] * 60, 500, places=6)
        self.assertAlmostEqual(native[2]['speed'] * 60, 250, places=6)
        self.assertEqual(min(range(3), key=lambda i: native[i]['speed']), 2)
        self.assertEqual([point.speed for point in points], list(speeds))

    def test_2600m_20minute_payload_curve_and_summary_share_pace_units(self):
        builder = RunningTrackBuilder(1, 2, 6, uuid_str='offline-pace-regression')
        points, fixed = builder.generate_track_around_points(30, 120,
            total_distance_m=2600, total_time_sec=1200, start_time_ms=1700000000000)
        body, obs, _ = builder.build_submission_payload(2600, 1200, 3533,
            1700000000000, 1700001200000, points, fixed)
        native = json.loads(body['allLocJson'])
        self.assertEqual(body['speed'], 7692)
        self.assertTrue(all(7.68 < point['speed'] < 7.71 for point in native))
        self.assertEqual(json.loads(decode(obs['run_data']))['allLocJson'], body['allLocJson'])
        # Only the displayed pace field changes; internal GPS speeds stay m/s.
        self.assertTrue(all(2.16 < point.speed < 2.18 for point in points))

    def test_zero_point_speed_preserves_the_existing_finite_sentinel(self):
        points = [GpsPoint(30, 120, 0, speed=0), GpsPoint(30.001, 120, 10000, speed=2)]
        native = map_points(points, 0, 10, 20, 20)
        self.assertEqual(native[0]['speed'], 0)
        self.assertTrue(all(math.isfinite(point['speed']) for point in native))

    def test_record_is_accepted_before_obs_and_id_path_is_written(self):
        client = self.client()
        uploaded = []

        def upload(url, headers, payload):
            client.submit_record.assert_called_once()
            self.assertEqual(decode(payload['rrid']), '1345698889')
            uploaded.append(payload)
            return {'error': 10000}

        client.upload_to_obs.side_effect = upload
        with patch('running.client.time.sleep'):
            result = client.execute_full_run(2150, 760)
        self.assertTrue(result.success)
        self.assertTrue(result.obs_uploaded)
        self.assertTrue(result.obs_verified)
        self.assertIsNone(result.is_valid)
        self.assertEqual(len(uploaded), 2)
        self.assertEqual(uploaded[0], uploaded[1])
        keys = [call.kwargs['object_key'] for call in client.request_obs_upload_url.call_args_list]
        self.assertIn('run_data/1345/1345698889.json', keys)
        self.assertTrue(any('/2023111506/' in key for key in keys))

    def test_rejected_record_does_not_upload_or_request_obs(self):
        client = self.client({'error': 10003, 'message': 'test rejection'})
        with patch('running.client.time.sleep'):
            result = client.execute_full_run(2150, 760)
        self.assertFalse(result.success)
        client.request_obs_upload_url.assert_not_called()
        client.upload_to_obs.assert_not_called()

    def test_obs_failure_preserves_accepted_record_and_never_resubmits(self):
        client = self.client()
        client.upload_to_obs.return_value = {'error': -1, 'message': 'offline failure'}
        with patch('running.client.time.sleep'):
            result = client.execute_full_run(2150, 760)
        self.assertTrue(result.success)
        self.assertEqual(result.rrid, 1345698889)
        self.assertFalse(result.obs_uploaded)
        self.assertFalse(result.obs_verified)
        self.assertIsNone(result.is_valid)
        client.submit_record.assert_called_once()
        client.verify_obs_object.assert_not_called()

    def test_readback_mismatch_does_not_claim_map_data_verified(self):
        client = self.client()
        client.verify_obs_object.return_value = {'error': -1, 'message': 'mismatched object'}
        with patch('running.client.time.sleep'):
            result = client.execute_full_run(2150, 760)
        self.assertTrue(result.success)
        self.assertTrue(result.obs_uploaded)
        self.assertFalse(result.obs_verified)
        self.assertIsNone(result.is_valid)
        client.submit_record.assert_called_once()

    def test_missing_record_id_does_not_upload_unbound_object(self):
        client = self.client({'error': 10000, 'data': {}})
        with patch('running.client.time.sleep'):
            result = client.execute_full_run(2150, 760)
        self.assertTrue(result.success)
        self.assertIsNone(result.rrid)
        client.request_obs_upload_url.assert_not_called()
        client.upload_to_obs.assert_not_called()

    def test_readback_uses_get_and_compares_actual_object(self):
        client = self.client()
        payload = {'rrid': 'encoded-test-value'}
        client.verify_obs_object = RunningClient.verify_obs_object.__get__(client)
        response = io.BytesIO(json.dumps(payload).encode('utf-8'))
        with patch('running.client.urllib.request.urlopen', return_value=response) as request:
            result = client.verify_obs_object('run_data/1345/1345698889.json', payload)
        self.assertEqual(result['error'], 10000)
        self.assertEqual(request.call_args.args[0].method, 'GET')
        client.request_obs_upload_url.assert_called_once_with(
            object_key='run_data/1345/1345698889.json', method='Get')

    def test_actual_readback_rejects_other_record_or_invalid_json(self):
        client = self.client()
        client.verify_obs_object = RunningClient.verify_obs_object.__get__(client)
        for content in (b'{"rrid":"other-record"}', b'not-json'):
            with self.subTest(content=content):
                with patch('running.client.urllib.request.urlopen', return_value=io.BytesIO(content)):
                    result = client.verify_obs_object('run_data/1/2.json', {'rrid': 'expected'})
                self.assertNotEqual(result['error'], 10000)

    def test_failed_readback_credentials_do_not_make_get_request(self):
        client = self.client()
        client.verify_obs_object = RunningClient.verify_obs_object.__get__(client)
        client.request_obs_upload_url.return_value = {'error': -1}
        with patch('running.client.urllib.request.urlopen') as request:
            result = client.verify_obs_object('run_data/1/2.json', {})
        self.assertNotEqual(result['error'], 10000)
        request.assert_not_called()

    def test_detail_exception_preserves_the_accepted_record(self):
        client = self.client()
        client.history.get_one_record.side_effect = ValueError('malformed detail')
        with patch('running.client.time.sleep'):
            result = client.execute_full_run(2150, 760)
        self.assertTrue(result.success)
        self.assertEqual(result.rrid, 1345698889)
        self.assertIsNone(result.is_valid)
        client.submit_record.assert_called_once()

    def test_cli_distinguishes_pending_verification_from_a_pass(self):
        spec = importlib.util.spec_from_file_location('obs_root_cli', Path(__file__).with_name('cli.py'))
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        client = Mock()
        client.execute_run.return_value = SimpleNamespace(success=True, rrid=123,
            total_distance=2150, total_time=760, is_valid=None,
            message='成绩已提交，但轨迹未完整确认；不要重复提交')
        with patch('sys.argv', ['cli.py', '-t', 'DUMMY', '--uid', '1', '--run']), \
                patch.object(cli, 'SportClient792', return_value=client), \
                patch('sys.stdout', new_callable=io.StringIO) as output:
            cli.main()
        self.assertIn('状态=待核验', output.getvalue())
        self.assertNotIn('状态=达标', output.getvalue())
        self.assertIn('不要重复提交', output.getvalue())


if __name__ == '__main__':
    unittest.main()
