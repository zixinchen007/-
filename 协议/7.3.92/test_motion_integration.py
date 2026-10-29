"""The opt-in timeline must drive every representation of a simulated run."""
import base64
import gzip
import hashlib
import json
import math
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from running.builder import RunningTrackBuilder
from running.client import RunningClient
from running.motion import MotionOptions, build_timeline
from sport_client import SportClient792
from test_loop_route import stadium
from test_run_consistency import measured_length


def stable_segment_length(first, last):
    # atan2(cross, dot) stays stable for sub-meter segments; acos(dot) does not.
    vectors = []
    for point in (first, last):
        lat, lon = math.radians(point.lat), math.radians(point.lon)
        vectors.append((math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)))
    a, b = vectors
    cross = (a[1]*b[2] - a[2]*b[1], a[2]*b[0] - a[0]*b[2], a[0]*b[1] - a[1]*b[0])
    return 6371000 * math.atan2(math.hypot(*cross), sum(x*y for x, y in zip(a, b)))


class MotionIntegrationTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        guard.start()
        self.addCleanup(guard.stop)

    def build(self):
        timeline = build_timeline(2537, 1183, MotionOptions(seed=42))
        loop = stadium()
        targets = [{'lat': loop[i][0], 'lng': loop[i][1], 'position': n + 1}
                   for n, i in enumerate((0, 33, 16, 49, 8))]
        builder = RunningTrackBuilder(1, 2, 1, uuid_str='offline-motion')
        points, fixed = builder.generate_track_around_points(30, 120, targets,
            2537, 1183, 1700000000000, route_loop=loop, timeline=timeline)
        body, obs, _ = builder.build_submission_payload(2537, 1183, timeline.total_steps,
            1700000000000, 1700001183000, points, fixed, timeline=timeline)
        return timeline, builder, points, fixed, body, obs

    def test_route_and_all_counters_follow_the_same_timeline(self):
        timeline, _, points, fixed, body, obs = self.build()
        self.assertAlmostEqual(measured_length(points), 2537, delta=.1)
        self.assertTrue(all(a.time < b.time for a, b in zip(points, points[1:])))
        cursor = 0
        for target in fixed:
            cursor = next(i for i in range(cursor, len(points))
                          if points[i].lat == target.lat and points[i].lon == target.lon) + 1
        native = json.loads(body['allLocJson'])
        traveled = 0.0
        for index, (gps, point) in enumerate(zip(points, native)):
            distance, steps = timeline.counters_at(gps.time - 1700000000000)
            if index:
                traveled += stable_segment_length(points[index - 1], gps)
            self.assertAlmostEqual(traveled, distance, delta=.05)
            self.assertEqual(point['totalDis'], round(distance, 4))
            self.assertEqual(point['steps'], round(steps))
            self.assertAlmostEqual(point['speed'] * gps.speed, 1000 / 60, places=8)
        self.assertEqual(native[-1]['steps'], body['totalSteps'])
        self.assertEqual(body['totalSteps'], timeline.total_steps)
        self.assertEqual(body['avgStepFreq'], round(timeline.total_steps * 60 / 1183))
        self.assertEqual(body['speed'], round(1183 / 60 / 2.537 * 1000))
        decoded = json.loads(gzip.decompress(base64.b64decode(obs['run_data'])))
        self.assertEqual(decoded['allLocJson'], body['allLocJson'])
        self.assertEqual(sum(row['stepsNum'] for row in body['stepsPerTenSec']), timeline.total_steps)
        self.assertAlmostEqual(sum(row['distance'] for row in body['speedPerTenSec']), 2537)
        self.assertEqual(body['speedPerTenSec'][-1]['endTime'] - body['speedPerTenSec'][-1]['beginTime'], 3000)
        self.assertGreater(len({row['distance'] for row in body['speedPerTenSec']}), 10)
        self.assertGreater(len({row['stepsNum'] for row in body['stepsPerTenSec'][:-1]}), 1)
        for distance_row, step_row in zip(body['speedPerTenSec'], body['stepsPerTenSec']):
            d0, s0 = timeline.counters_at(distance_row['beginTime'] - 1700000000000)
            d1, s1 = timeline.counters_at(distance_row['endTime'] - 1700000000000)
            self.assertAlmostEqual(distance_row['distance'], d1 - d0, delta=.01)
            self.assertEqual(step_row['stepsNum'], round(s1) - round(s0))

    def test_mismatched_timeline_or_steps_are_rejected(self):
        timeline, builder, points, fixed, _, _ = self.build()
        with self.assertRaises(ValueError):
            builder.build_submission_payload(2537, 1183, timeline.total_steps + 1,
                1700000000000, 1700001183000, points, fixed, timeline=timeline)
        with self.assertRaises(ValueError):
            builder.generate_track_around_points(30, 120, total_distance_m=2600,
                total_time_sec=1183, timeline=timeline)

    def test_legacy_default_is_identical_to_explicit_none(self):
        builder = RunningTrackBuilder(1, 2, 6, uuid_str='legacy-identical')
        args = dict(total_distance_m=2537, total_time_sec=1183, start_time_ms=1700000000000)
        first, fixed = builder.generate_track_around_points(30, 120, **args)
        second, other_fixed = builder.generate_track_around_points(30, 120, **args, timeline=None)
        self.assertEqual(first, second)
        one = builder.build_submission_payload(2537, 1183, 3500, 1700000000000,
                                               1700001183000, first, fixed)[0]
        two = builder.build_submission_payload(2537, 1183, 3500, 1700000000000,
                                               1700001183000, second, other_fixed, timeline=None)[0]
        self.assertEqual(one, two)

    def test_legacy_payloads_match_frozen_baseline_commit(self):
        # Canonical payloads generated from 0c182ee in a temporary checkout,
        # using this machine's Python 3.13; catches changes outside the opt-in path.
        cases = ((2537, 1183, 3500, '98b0c12be44aa3426bd1273763e88062c5a793b18abc29e3c2bb05dfe3f50db1'),
                 (2600, 1200, 3533, 'b78d1dd0984b0979c156f51d2864b4feee14785656416ff335fbbc125a7650d7'))
        for meters, seconds, steps, expected in cases:
            builder = RunningTrackBuilder(1, 2, 6, uuid_str='legacy-identical')
            points, fixed = builder.generate_track_around_points(30, 120,
                total_distance_m=meters, total_time_sec=seconds, start_time_ms=1700000000000)
            body = builder.build_submission_payload(meters, seconds, steps, 1700000000000,
                1700000000000 + seconds * 1000, points, fixed)[0]
            digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            self.assertEqual(digest, expected)

    def test_running_client_uses_timeline_step_total_and_facade_forwards_options(self):
        options = MotionOptions(seed=42)
        client = RunningClient(uid=1, token='DUMMY', unid=2, device_config={'DeviceId': '1' * 32})
        client.profile.get_server_time = Mock(return_value=SimpleNamespace(
            server_timestamp_ms=1700001183000, offset_seconds=0))
        client.policy.get_run_policy = Mock(return_value=SimpleNamespace(min_distance=2000, raw={}))
        client.policy.get_geo_fence = Mock(return_value=[])
        client.telemetry.send_startup_pv = Mock()
        client.telemetry.send_page_exposure = Mock()
        client.submit_record = Mock(return_value={'error': -1, 'message': 'offline stop'})
        result = client.execute_full_run(2537, 1183, sport_type=6, motion_options=options)
        self.assertFalse(result.success)
        body = client.submit_record.call_args.args[0]
        self.assertEqual(body['totalSteps'], build_timeline(2537, 1183, options).total_steps)
        facade = SportClient792(uid=1, token='DUMMY', unid=2, device_config={'DeviceId': '1' * 32})
        facade.running.execute_full_run = Mock()
        facade.execute_run(motion_options=options)
        self.assertEqual(facade.running.execute_full_run.call_args.kwargs['motion_options'], options)


if __name__ == '__main__':
    unittest.main()
