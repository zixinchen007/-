"""Offline checks for laps that follow a supplied running track."""
import math
import importlib.util
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from running.builder import RunningTrackBuilder
from running.client import RunningClient
from running.consistency import distance
from running.obs import bd09_to_gcj02
from sport_client import SportClient792


SCALE = math.pi * 6371000 / 180
RADIUS = 36.5
HALF_STRAIGHT = (400 - 2 * math.pi * RADIUS) / 4


def geo(x, y):
    return 30 + y / SCALE, 120 + x / (SCALE * math.cos(math.radians(30)))


def xy(point):
    return ((point.lon - 120) * SCALE * math.cos(math.radians(30)),
            (point.lat - 30) * SCALE)


def stadium():
    return [geo(RADIUS * math.cos(i * math.pi / 32),
                offset + RADIUS * math.sin(i * math.pi / 32))
            for offset, indices in ((HALF_STRAIGHT, range(33)),
                                    (-HALF_STRAIGHT, range(32, 65)))
            for i in indices]


class LoopRouteTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        guard.start()
        self.addCleanup(guard.stop)

    def build(self, meters=2400, checkpoints=None, loop=None):
        builder = RunningTrackBuilder(1, 2, 1 if checkpoints else 6)
        return builder.generate_track_around_points(
            30, 120, checkpoints, meters, 1020, 1700000000000,
            route_loop=stadium() if loop is None else loop)

    def assert_on_track(self, points, margin=.1):
        for point in points:
            x, y = xy(point)
            # Distance from the stadium's central straight line, in meters.
            radius = math.hypot(x, max(0, abs(y) - HALF_STRAIGHT))
            self.assertGreaterEqual(radius, RADIUS - margin)

    def test_one_lap_is_closed_and_does_not_cross_the_infield(self):
        points, fixed = self.build(meters=400)
        self.assertEqual(fixed, [])
        self.assertLess(distance((points[0].lat, points[0].lon),
                                 (points[-1].lat, points[-1].lon)), .2)
        self.assert_on_track(points)
        self.assertAlmostEqual(sum(distance((a.lat, a.lon), (b.lat, b.lon))
                                   for a, b in zip(points, points[1:])), 400, delta=.05)

    def test_checkpoints_keep_coordinates_metadata_and_order_across_laps(self):
        loop = stadium()
        targets = [{'lat': loop[i][0], 'lng': loop[i][1], 'name': str(n),
                    'position': n + 1, 'id': 100 + n}
                   for n, i in enumerate((0, 33, 16, 49, 8))]
        points, fixed = self.build(checkpoints=targets)
        cursor = 0
        for actual, target in zip(fixed, targets):
            self.assertEqual((actual.lat, actual.lon), (target['lat'], target['lng']))
            self.assertEqual(actual.raw['id'], target['id'])
            cursor = next(i for i in range(cursor, len(points))
                          if distance((points[i].lat, points[i].lon),
                                      (actual.lat, actual.lon)) < .1) + 1
        self.assert_on_track(points)
        self.assertEqual(points[-1].time - points[0].time, 1020000)
        self.assertTrue(all(a.time < b.time for a, b in zip(points, points[1:])))
        with self.assertRaisesRegex(ValueError, '顺序'):
            self.build(meters=400, checkpoints=targets)

    def test_nearby_checkpoint_uses_short_access_path(self):
        lat, lon = geo(RADIUS + 5, HALF_STRAIGHT)
        points, fixed = self.build(checkpoints=[{'lat': lat, 'lng': lon}])
        self.assertEqual((points[0].lat, points[0].lon), (lat, lon))
        self.assertEqual(len(fixed), 1)
        self.assert_on_track(points)

    def test_far_checkpoint_and_invalid_loops_are_rejected(self):
        with self.assertRaisesRegex(ValueError, '25'):
            self.build(checkpoints=[{'lat': 31, 'lng': 121}])
        invalid = ([], [(30, 120), (30.001, 120)],
                   [(30, 120), (91, 120), (30.001, 120)],
                   [geo(-30, -30), geo(30, 30), geo(-30, 30), geo(30, -30)])
        for loop in invalid:
            with self.subTest(loop=loop), self.assertRaises(ValueError):
                self.build(loop=loop)

    def test_explicit_loop_requires_official_checkpoints_in_counted_mode(self):
        builder = RunningTrackBuilder(1, 2, 1)
        with self.assertRaisesRegex(ValueError, '官方点位'):
            builder.generate_track_around_points(30, 120, route_loop=stadium())

    def test_facade_passes_explicit_route_to_running_client(self):
        client = SportClient792(uid=1, token='DUMMY', unid=2,
                                device_config={'DeviceId': '1' * 32})
        client.running.execute_full_run = Mock()
        loop = stadium()
        client.execute_run(route_loop=loop)
        self.assertEqual(client.running.execute_full_run.call_args.kwargs['route_loop'], loop)

    def test_running_client_packages_the_selected_loop(self):
        client = RunningClient(uid=1, token='DUMMY', unid=2, device_config={'DeviceId': '1' * 32})
        client.policy.get_run_policy = Mock(return_value=SimpleNamespace(min_distance=2000, raw={}))
        client.profile.get_server_time = Mock(return_value=SimpleNamespace(
            server_timestamp_ms=1700001020000, offset_seconds=0))
        client.telemetry.send_startup_pv = Mock()
        client.telemetry.send_page_exposure = Mock()
        client.policy.get_geo_fence = Mock(return_value=[])
        client.submit_record = Mock(return_value={'error': -1, 'message': 'offline stop'})
        result = client.execute_full_run(2400, 1020, sport_type=6, route_loop=stadium())
        self.assertFalse(result.success)
        body = client.submit_record.call_args.args[0]
        lat0, lon0 = bd09_to_gcj02(30, 120)
        for point in json.loads(body['allLocJson']):
            x = (point['gLng'] - lon0) * SCALE * math.cos(math.radians(30))
            y = (point['gLat'] - lat0) * SCALE
            self.assertGreater(math.hypot(x, max(0, abs(y) - HALF_STRAIGHT)), RADIUS - .5)

    def test_cli_loads_bd09_route_file_before_login_or_submission(self):
        spec = importlib.util.spec_from_file_location('loop_test_cli', Path(__file__).with_name('cli.py'))
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'loop.json'
            for coor_type in ('gcj02', 'bd09'):
                path.write_text(json.dumps({'coord_type': coor_type, 'loop': stadium()}), encoding='utf-8')
                client = Mock(token='DUMMY', uid=1)
                client.execute_run.return_value = SimpleNamespace(success=False, error_code=-1, message='offline stop')
                with patch.object(cli, 'SportClient792', return_value=client) as factory, \
                        patch.object(sys, 'argv', ['cli.py', '--run', '--route-file', str(path)]), \
                        patch('sys.stdout', new_callable=io.StringIO), \
                        patch('sys.stderr', new_callable=io.StringIO):
                    if coor_type == 'gcj02':
                        with self.assertRaises(SystemExit):
                            cli.main()
                        factory.assert_not_called()
                    else:
                        cli.main()
                        actual = client.execute_run.call_args.kwargs['route_loop']
                        self.assertEqual([tuple(point) for point in actual], stadium())


if __name__ == '__main__':
    unittest.main()
