"""Offline acceptance tests for a reproducible, consistent jogging timeline."""
import math
import socket
import unittest
from unittest.mock import patch

from running.motion import MotionOptions, build_timeline


class MotionTimelineTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_same_seed_repeats_and_other_seed_changes_the_shape(self):
        first = build_timeline(2537, 1183, MotionOptions(seed=42))
        self.assertEqual(first, build_timeline(2537, 1183, MotionOptions(seed=42)))
        second = build_timeline(2537, 1183, MotionOptions(seed=43))
        self.assertNotEqual(first.speeds_m_s, second.speeds_m_s)
        self.assertNotEqual(first.cadences_spm, second.cadences_spm)
        generated = build_timeline(2537, 1183)
        self.assertEqual(generated, build_timeline(2537, 1183, MotionOptions(seed=generated.seed)))

    def test_exact_targets_smooth_jogging_and_coupled_cadence(self):
        for meters, seconds in ((2537, 1183), (2600, 1200), (150, 60)):
            with self.subTest(meters=meters, seconds=seconds):
                timeline = build_timeline(meters, seconds, MotionOptions(seed=42))
                mean = meters / seconds
                self.assertEqual(timeline.duration_s, seconds)
                self.assertEqual(len(timeline.speeds_m_s), seconds + 1)
                self.assertAlmostEqual(timeline.total_distance_m, meters, places=7)
                self.assertAlmostEqual(sum((a + b) / 2 for a, b in zip(
                    timeline.speeds_m_s, timeline.speeds_m_s[1:])), meters, places=6)
                self.assertTrue(all(.95 * mean - 1e-9 <= v <= 1.05 * mean + 1e-9
                                    for v in timeline.speeds_m_s[10:-10]))
                self.assertLess(timeline.speeds_m_s[0], .91 * mean)
                self.assertLess(timeline.speeds_m_s[-1], .96 * mean)
                self.assertTrue(all(125 <= c <= 145 for c in timeline.cadences_spm))
                self.assertLess(max(abs(a - b) for a, b in zip(
                    timeline.speeds_m_s, timeline.speeds_m_s[1:])), .08 * mean)
                expected_steps = sum((a + b) / 120 for a, b in zip(
                    timeline.cadences_spm, timeline.cadences_spm[1:]))
                self.assertEqual(timeline.total_steps, round(expected_steps))

    def test_interpolated_counters_and_inverse_distance_are_consistent(self):
        timeline = build_timeline(2537, 1183, MotionOptions(seed=13))
        for elapsed in (0, 1, 125.5, 10000, 613777, 1183000):
            meters, steps = timeline.counters_at(elapsed)
            self.assertAlmostEqual(timeline.time_at_distance(meters), elapsed, delta=.001)
            self.assertGreaterEqual(steps, 0)
            self.assertTrue(math.isfinite(timeline.speed_at(elapsed)))
        self.assertEqual(timeline.counters_at(1183000),
                         (timeline.total_distance_m, timeline.cumulative_steps[-1]))
        with self.assertRaises(ValueError):
            timeline.validate_target(2600, 1183)
        with self.assertRaises(ValueError):
            timeline.validate_target(2537, 1183, timeline.total_steps + 1)

    def test_infeasible_or_invalid_inputs_fail_instead_of_changing_targets(self):
        cases = ((0, 1200, MotionOptions()), (2600, 59, MotionOptions()),
                 (2600, 1200, MotionOptions(seed=True)),
                 (2600, 1200, MotionOptions(variation=0)),
                 (2600, 1200, MotionOptions(cadence_center=150)),
                 (2600, 1200, MotionOptions(min_speed_m_s=2.3)),
                 (2600, 1200, MotionOptions(max_speed_m_s=2.0)),
                 (2600, 1200, MotionOptions(correlation_seconds=float('nan'))))
        for meters, seconds, options in cases:
            with self.subTest(meters=meters, seconds=seconds, options=options), self.assertRaises(ValueError):
                build_timeline(meters, seconds, options)


if __name__ == '__main__':
    unittest.main()
