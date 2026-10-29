"""Reference-model acceptance tests use synthetic meter coordinates only."""
import math
import socket
import unittest
from dataclasses import replace
from unittest.mock import patch

from running.motion_v3 import MotionV3Options, MotionRoute, build_timeline, round_steps


def straight(meters=2000):
    return MotionRoute.from_xy(((0., 0.), (meters, 0.)))


def ring(meters=2300, radius=60, count=120):
    points = [(radius * math.cos(i * math.tau / count),
               radius * math.sin(i * math.tau / count)) for i in range(count)]
    points.append(points[0])
    return MotionRoute.loop(points, meters)


def sharp_route():
    points=[(0.,-1.5),(100.,-1.5)]
    for i in range(1,5):
        a=-math.pi/2+i*math.pi/4
        points.append((100+1.5*math.cos(a),1.5*math.sin(a)))
    points.append((0.,1.5))
    for i in range(1,4):
        a=math.pi/2+i*math.pi/4
        points.append((1.5*math.cos(a),1.5*math.sin(a)))
    points.append(points[0])
    return MotionRoute.loop(points,2000.)


class MotionV3Tests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_integrals_targets_constraints_and_inverse(self):
        for route, seconds in ((straight(), 800), (ring(), 900)):
            t = build_timeline(route, seconds, MotionV3Options(seed=7))
            self.assertEqual(t.speeds_m_s[0], 0.)
            self.assertEqual(t.speeds_m_s[-1], 0.)
            self.assertAlmostEqual(t.total_distance_m, route.length_m, delta=1e-7)
            for i in range(seconds):
                v0, v1 = t.speeds_m_s[i:i+2]
                self.assertLessEqual(v1 - v0, .65 + 1e-9)
                self.assertLessEqual(v0 - v1, 1. + 1e-9)
                self.assertTrue(0 <= v1 <= 6.3)
                self.assertAlmostEqual(t.cumulative_distances_m[i+1] - t.cumulative_distances_m[i],
                                       (v0 + v1)/2, delta=1e-9)
                self.assertAlmostEqual(t.cumulative_steps[i+1] - t.cumulative_steps[i],
                                       t.interval_cadences_spm[i]/60, delta=1e-9)
            for ms in (0, 1, 1000.25, 456123.5, seconds*1000):
                d, _ = t.counters_at(ms)
                self.assertAlmostEqual(t.time_at_distance(d), ms, delta=.001)
            t.validate_target(route.length_m, seconds, t.total_steps)
            self.assertEqual(t.total_steps, round_steps(t.cumulative_steps[-1]))
            self.assertEqual(t, build_timeline(route, seconds, MotionV3Options(seed=7)))
            self.assertNotEqual(t.speeds_m_s,
                                build_timeline(route, seconds, MotionV3Options(seed=8)).speeds_m_s)

    def test_invalid_infeasible_inputs_and_half_rounding(self):
        for seconds in (True, 0, 1, 86401, 800.5):
            with self.assertRaises(ValueError):
                build_timeline(straight(), seconds)
        for options in (MotionV3Options(seed=True), MotionV3Options(seed=-1),
                        MotionV3Options(accel_m_s2=0), MotionV3Options(max_speed_m_s=float('nan'))):
            with self.assertRaises(ValueError):
                build_timeline(straight(), 800, options)
        with self.assertRaises(ValueError):
            build_timeline(straight(), 10)
        for points in (((0, 0), (0, 0)), ((0, 0), (float('nan'), 1))):
            with self.assertRaises(ValueError):
                MotionRoute.from_xy(points)
        self.assertEqual(round_steps(2.5), 3)
        self.assertEqual(round_steps(.5), 1)

    def test_reference_steady_running_thresholds(self):
        rms = lambda a: math.sqrt(sum(v*v for v in a)/len(a))
        for seconds in (500, 800, 1200):
            for seed in range(20):
                t = build_timeline(straight(1928.), seconds, MotionV3Options(seed=seed))
                v = t.speeds_m_s[30:-30]
                mean = sum(v)/len(v)
                accel = [b-a for a,b in zip(v,v[1:])]
                jerk = [b-a for a,b in zip(accel,accel[1:])]
                c = t.interval_cadences_spm[30:-30]
                with self.subTest(seconds=seconds, seed=seed):
                    self.assertTrue(.003 < rms([x-mean for x in v])/mean < .08)
                    self.assertLess(rms(accel)/mean, .004)
                    self.assertLess(rms(jerk)/mean, .004)
                    self.assertLess(rms([b-a for a,b in zip(c,c[1:])]), .4)

    def test_tight_bends_and_lap_seams(self):
        r = ring(800, radius=3, count=60)
        t = build_timeline(r, 500, MotionV3Options(seed=7))
        for v in t.speeds_m_s:
            self.assertLessEqual(v*v/3, 2.5 + .1)
        self.assertAlmostEqual(t.total_distance_m, 800, delta=1e-7)

    def test_bend_postcheck_rejects_invalid_timeline(self):
        route=sharp_route()
        t=build_timeline(route,800,MotionV3Options(seed=7))
        t=replace(t, speeds_m_s=tuple(v*2 for v in t.speeds_m_s))
        self.assertGreater(route.max_lateral_acceleration(t),2.6)
        with self.assertRaisesRegex(ValueError,'弯道'):
            route.validate_bends(t,2.5)
        smooth=ring(2000.)
        smooth.validate_bends(build_timeline(smooth,800,MotionV3Options(seed=7)),2.5)

    def test_sharp_vertices_respect_limits_between_second_samples(self):
        route = sharp_route()
        for seconds, seed in ((800,7), (800,42), (960,7), (960,42)):
            with self.subTest(seconds=seconds,seed=seed):
                timeline = build_timeline(route, seconds, MotionV3Options(seed=seed))
                self.assertLessEqual(route.max_lateral_acceleration(timeline), 2.5 + 1e-7)
                self.assertAlmostEqual(timeline.total_distance_m, route.length_m, delta=1e-7)
                for a, b in zip(timeline.speeds_m_s, timeline.speeds_m_s[1:]):
                    self.assertLessEqual(b-a, .65 + 1e-9)
                    self.assertLessEqual(a-b, 1. + 1e-9)

    def test_subsecond_hairpin_with_low_corner_speed(self):
        route = MotionRoute.loop(((0.,0.), (100.,0.), (100.01,0.),
                                  (100.,0.), (0.,0.)), 500.)
        self.assertLess(math.sqrt(2.5/max(route.curvatures)), 1.)
        timeline = build_timeline(route, 240, MotionV3Options(seed=42))
        route.validate_bends(timeline, 2.5, tolerance=1e-7)
        self.assertAlmostEqual(timeline.total_distance_m, 500., delta=1e-7)


if __name__ == '__main__':
    unittest.main()
