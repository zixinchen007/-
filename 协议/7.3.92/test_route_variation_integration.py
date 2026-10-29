"""The geometry changes; the canonical motion counters do not."""
import json
import socket
import unittest
from unittest.mock import patch

from running.builder import RunningTrackBuilder
from running.consistency import distance
from running.motion import MotionOptions, build_timeline
from running.route_variation import RouteVariationOptions
from test_loop_route import stadium


class RouteVariationIntegrationTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        guard.start()
        self.addCleanup(guard.stop)

    def build(self, variation=None):
        timeline = build_timeline(2537, 1183, MotionOptions(seed=42))
        loop = stadium()
        targets = [{'lat': loop[i][0], 'lng': loop[i][1], 'position': n + 1, 'id': n + 10}
                   for n, i in enumerate((0, 33, 16, 49, 8))]
        builder = RunningTrackBuilder(0, 0, 1, uuid_str='offline-variation')
        points, fixed = builder.generate_track_around_points(30, 120, targets, 2537, 1183, 0,
            route_loop=loop, timeline=timeline, route_variation=variation)
        body, _, _ = builder.build_submission_payload(2537, 1183, timeline.total_steps,
            0, 1183000, points, fixed, timeline=timeline)
        return builder, timeline, points, fixed, body

    def test_disabled_and_zero_offset_preserve_full_body(self):
        first = self.build()
        second = self.build(RouteVariationOptions(max_offset_m=0, seed=42))
        self.assertEqual(first[2:], second[2:])
        self.assertIsNone(first[0].route_geometry)
        self.assertIsNone(second[0].route_geometry)

    def test_changed_geometry_keeps_motion_and_all_totals(self):
        _, _, baseline, _, original = self.build()
        builder, timeline, points, fixed, body = self.build(RouteVariationOptions(seed=42))
        self.assertNotEqual(baseline, points)
        self.assertEqual(body['speedPerTenSec'], original['speedPerTenSec'])
        self.assertEqual(body['stepsPerTenSec'], original['stepsPerTenSec'])
        self.assertEqual(body['totalSteps'], original['totalSteps'])
        self.assertEqual(body['fivePointJson'], original['fivePointJson'])
        self.assertEqual(body['speedPerTenSec'][-1]['endTime'] - body['speedPerTenSec'][-1]['beginTime'], 3000)
        traveled = 0.0
        native = json.loads(body['allLocJson'])
        for index, (gps, wire) in enumerate(zip(points, native)):
            expected, steps = timeline.counters_at(gps.time)
            if index:
                traveled += distance((points[index-1].lat, points[index-1].lon), (gps.lat, gps.lon))
            self.assertAlmostEqual(traveled, expected, delta=.01)
            self.assertEqual(wire['totalDis'], round(expected, 4))
            self.assertEqual(wire['steps'], round(steps))
            self.assertAlmostEqual(wire['speed'] * gps.speed, 1000 / 60, places=8)
        self.assertEqual(len(builder.route_geometry.required_indices), 5)
        cursor = 0
        for target in fixed:
            cursor = next(i for i in range(cursor, len(points)) if
                distance((points[i].lat, points[i].lon), (target.lat, target.lon)) < .1) + 1

    def test_variation_requires_explicit_route_and_timeline(self):
        builder = RunningTrackBuilder(0, 0, 6)
        with self.assertRaisesRegex(ValueError, '跑道|时间序列'):
            builder.generate_track_around_points(30, 120, route_variation=RouteVariationOptions(seed=42))
        with self.assertRaisesRegex(ValueError, '时间序列'):
            builder.generate_track_around_points(30, 120, route_loop=stadium(),
                route_variation=RouteVariationOptions(seed=42))


if __name__ == '__main__':
    unittest.main()
