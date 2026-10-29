"""Geometry-only tests: no account, device state, or network clients."""
import math
from types import SimpleNamespace
import socket
import unittest
from unittest.mock import patch

from running.consistency import distance
from running.route_variation import RouteVariationOptions, build_varied_route
from test_loop_route import stadium, xy, geo, RADIUS, HALF_STRAIGHT


class RouteVariationTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect', 'core.local_state.load_session', 'core.config.get_device_id'):
            guard = patch(target, side_effect=AssertionError('network/account state forbidden'))
            guard.start()
            self.addCleanup(guard.stop)

    def targets(self):
        ring = stadium()
        return [SimpleNamespace(lat=ring[i][0], lon=ring[i][1], position=n + 1)
                for n, i in enumerate((0, 33, 16, 49, 8))]

    def build(self, seed=42, targets=None, meters=2537, ring=None):
        return build_varied_route(stadium() if ring is None else ring,
            self.targets() if targets is None else targets, meters, RouteVariationOptions(seed=seed))

    def test_seed_bounds_spacing_and_outside_reference(self):
        route = self.build()
        self.assertEqual(route, self.build())
        self.assertNotEqual(route.points, self.build(seed=43).points)
        self.assertTrue(all(0 <= value <= 2 for value in route.controls))
        self.assertTrue(all(abs(a - b) <= .50000001 for a, b in zip(route.controls, route.controls[1:])))
        self.assertGreaterEqual(route.cumulative_m[-1], 2537)
        self.assertLess(route.cumulative_m[-1] - 2537, 5.001)
        self.assertTrue(all(distance(a, b) <= 5.000001 for a, b in zip(route.points, route.points[1:])))
        self.assertTrue(all(a <= b + 1e-7 for a, b in zip(route.stations_m, route.stations_m[1:])))
        for pair, offset, connector in zip(route.points, route.offsets_m, route.connectors):
            self.assertGreaterEqual(offset, 0)
            self.assertLessEqual(offset, 2)
            if not connector:
                x, y = xy(SimpleNamespace(lat=pair[0], lon=pair[1]))
                radius = math.hypot(x, max(0, abs(y) - HALF_STRAIGHT))
                # The base oval is polygonal; tolerate its existing chord sag.
                self.assertGreaterEqual(radius, RADIUS - .05)
                self.assertLessEqual(radius, RADIUS + 2.001)

    def test_five_ordered_anchors_only_once_and_later_laps_are_free(self):
        targets = self.targets()
        route = self.build(targets=targets)
        self.assertEqual(len(route.required_indices), 5)
        self.assertEqual(list(route.required_indices), sorted(route.required_indices))
        for index, target in zip(route.required_indices, targets):
            self.assertEqual(route.points[index], (target.lat, target.lon))
            self.assertEqual(route.offsets_m[index], 0)
        last_station = route.stations_m[route.required_indices[-1]]
        for station in route.anchor_stations_m:
            later = station + route.perimeter_m * 5
            samples = [offset for q, offset in zip(route.stations_m, route.offsets_m)
                       if abs(q - later) < 5 and q > last_station + 25]
            if samples:
                self.assertGreater(max(samples), .01)

    def test_checkpoint_spur_is_preserved_and_distance_must_cover_anchors(self):
        targets = self.targets()
        targets[1] = SimpleNamespace(lat=targets[1].lat, lon=targets[1].lon - .00005, position=2)
        route = self.build(targets=targets)
        self.assertTrue(any(route.connectors))
        for index, target in zip(route.required_indices, targets):
            self.assertEqual(route.points[index], (target.lat, target.lon))
        with self.assertRaisesRegex(ValueError, '距离|顺序'):
            self.build(meters=100)

    def test_free_route_and_reverse_direction(self):
        for ring in (stadium(), list(reversed(stadium()))):
            route = self.build(targets=[], meters=835, ring=ring)
            self.assertEqual(route.required_indices, ())
            self.assertGreater(route.stations_m[-1], route.perimeter_m * 2)
            self.assertTrue(all(0 <= x <= 2 for x in route.offsets_m))

    def test_invalid_configuration_and_geometry_are_rejected(self):
        for maximum in (-1, 2.01, float('nan'), True):
            with self.subTest(maximum=maximum), self.assertRaises(ValueError):
                build_varied_route(stadium(), [], 1000, RouteVariationOptions(max_offset_m=maximum, seed=42))
        with self.assertRaises(ValueError):
            build_varied_route(stadium(), [], 1000, RouteVariationOptions(seed=True))
        with self.assertRaises(ValueError):
            build_varied_route([(30, 120), (30, 120.01)], [], 1000, RouteVariationOptions(seed=42))
        with self.assertRaisesRegex(ValueError, '过多'):
            self.build(targets=[], meters=10**12)

    def test_anchor_taper_and_lap_seams_have_bounded_lateral_change(self):
        route = self.build()
        last = route.anchor_stations_m[-1]
        for q, offset, connector in zip(route.stations_m, route.offsets_m, route.connectors):
            if connector:
                continue
            for anchor in route.anchor_stations_m:
                fraction = min(1, abs(q-anchor)/25)
                bound = 2 * fraction*fraction*(3-2*fraction)
                self.assertLessEqual(offset, bound + .01)
            if q > last + 25:
                index = int(q/50)
                fraction = q/50-index
                weight = fraction*fraction*(3-2*fraction)
                expected = route.controls[index]*(1-weight) + route.controls[index+1]*weight
                self.assertAlmostEqual(offset, expected, delta=.002)
        for index in range(1, len(route.points)):
            if route.connectors[index]:
                continue
            delta = route.stations_m[index] - route.stations_m[index-1]
            # .015 from the random curve, plus .12 from a 25m anchor taper.
            self.assertLessEqual(abs(route.offsets_m[index]-route.offsets_m[index-1]), .136*delta + 1e-6)

    def test_narrow_concave_notch_rejects_outward_excursion(self):
        ring = [geo(x, y) for x, y in ((0, 0), (100, 0), (100, 100), (50.05, 100),
                (50.05, 2), (49.95, 2), (49.95, 100), (0, 100))]
        with self.assertRaisesRegex(ValueError, '范围|自交'):
            self.build(targets=[], meters=1000, ring=ring)


if __name__ == '__main__':
    unittest.main()
