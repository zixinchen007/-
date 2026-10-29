"""Offline lap comparison and snapshot-region validation."""
import importlib.util
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from running.motion import MotionOptions
from running.preview import build_preview
from running.route_variation import RouteVariationOptions
from test_loop_route import stadium


class LapPreviewTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect', 'urllib.request.urlopen',
                       'sport_client.SportClient792.__init__', 'login.client.LoginClient.__init__',
                       'core.local_state.load_session', 'core.config.get_device_id'):
            guard = patch(target, side_effect=AssertionError('network/account state forbidden'))
            guard.start()
            self.addCleanup(guard.stop)

    def snapshot(self):
        return {'coord_type': 'bd09', 'captured_date': '2026-10-27', 'source': 'test geometry only',
                'polygons': [[[29.99, 119.99], [29.99, 120.01], [30.01, 120.01], [30.01, 119.99]]]}

    def test_comparison_reproduces_both_cases_and_preserves_motion(self):
        for meters, seconds, seed in ((2287, 1099, 4711337142983578327), (2537, 1183, 42)):
            options = MotionOptions(seed=seed)
            report = build_preview(meters, seconds, options, route_variation=RouteVariationOptions())
            varied = report['route_variation']['model']
            baseline = report['models']['jog']
            self.assertTrue(report['all_checks_passed'])
            self.assertEqual(varied['samples'], baseline['samples'])
            self.assertEqual(varied['step_windows'], baseline['step_windows'])
            self.assertEqual(varied['distance_windows'], baseline['distance_windows'])
            self.assertLessEqual(varied['summary']['max_lateral_offset_m'], 2)
            self.assertGreater(varied['summary']['complete_laps'], 4)
            self.assertEqual(report['route_variation']['boundary']['status'], 'unverified')
            self.assertEqual(report, build_preview(meters, seconds, options,
                             route_variation=RouteVariationOptions()))
        original = build_preview(2537, 1183, MotionOptions(seed=42))
        zero = build_preview(2537, 1183, MotionOptions(seed=42),
                             route_variation=RouteVariationOptions(max_offset_m=0))
        self.assertEqual(original, zero)

    def cli(self):
        spec = importlib.util.spec_from_file_location('lap_preview_entry', Path(__file__).with_name('preview_motion.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_cli_outputs_three_route_views_and_snapshot_provenance(self):
        with tempfile.TemporaryDirectory() as folder, patch('sys.stdout', new_callable=io.StringIO) as output:
            snapshot = Path(folder) / 'fences.json'
            snapshot.write_text(json.dumps(self.snapshot()), encoding='utf-8')
            self.assertEqual(self.cli().main(['--lap-variation', '--seed', '42', '--fences-file', str(snapshot),
                                             '--output-dir', str(Path(folder) / 'output')]), 0)
            paths = json.loads(output.getvalue())
            self.assertIn('lap2m', paths['html'])
            html = Path(paths['html']).read_text(encoding='utf-8')
            for label in ('按圈着色', '单色叠加', '弯道放大', '2026-10-27', '2米'):
                self.assertIn(label, html)
            self.assertNotIn('https://', html)
            self.assertNotIn('http://', html)
            report = json.loads(Path(paths['data']).read_text(encoding='utf-8'))
            self.assertEqual(report['route_variation']['boundary']['status'], 'snapshot_only')

    def test_outside_snapshot_does_not_write_partial_output(self):
        with tempfile.TemporaryDirectory() as folder, patch('sys.stderr', new_callable=io.StringIO):
            snapshot = self.snapshot()
            snapshot['polygons'] = [[[0, 0], [0, .01], [.01, .01], [.01, 0]]]
            source = Path(folder) / 'fences.json'
            source.write_text(json.dumps(snapshot), encoding='utf-8')
            target = Path(folder) / 'output'
            self.assertEqual(self.cli().main(['--lap-variation', '--seed', '42', '--fences-file', str(source),
                                             '--output-dir', str(target)]), 2)
            self.assertFalse(target.exists())

    def test_concave_region_checks_segment_interiors_and_union(self):
        from running.region_geometry import verify_region
        snapshot = {'coord_type': 'bd09', 'captured_date': '2026-10-27', 'source': 'test',
                    'polygons': [[[30, 120], [30, 120.004], [30.004, 120.004],
                                  [30.004, 120.003], [30.001, 120.003], [30.001, 120.001],
                                  [30.004, 120.001], [30.004, 120]]]}
        points = [{'lat': 30.003, 'lon': 120.0005}, {'lat': 30.003, 'lon': 120.0035}]
        with self.assertRaisesRegex(ValueError, '围栏'):
            verify_region(points, snapshot)
        snapshot['polygons'].append([[30.002, 120], [30.002, 120.004], [30.004, 120.004], [30.004, 120]])
        self.assertEqual(verify_region(points, snapshot)['status'], 'snapshot_only')
        snapshot['captured_date'] = 'not-a-date'
        with self.assertRaises(ValueError):
            verify_region(points, snapshot)


if __name__ == '__main__':
    unittest.main()
