"""Offline preview must produce reviewable data without loading an account."""
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


class MotionPreviewTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_comparison_reports_share_targets_and_expose_derived_stride(self):
        for meters, seconds in ((2537, 1183), (2600, 1200)):
            report = build_preview(meters, seconds, MotionOptions(seed=42))
            self.assertTrue(report['simulated'])
            self.assertEqual(report['seed'], 42)
            self.assertTrue(report['all_checks_passed'])
            old, jog = report['models']['constant'], report['models']['jog']
            self.assertEqual(old['summary']['distance_m'], jog['summary']['distance_m'])
            self.assertEqual(old['summary']['duration_s'], jog['summary']['duration_s'])
            self.assertEqual(jog['summary']['distance_m'], meters)
            self.assertEqual(jog['summary']['duration_s'], seconds)
            self.assertAlmostEqual(jog['summary']['average_pace_min_km'], seconds / 60 / (meters / 1000))
            self.assertGreater(max(x['cadence_spm'] for x in jog['samples']) -
                               min(x['cadence_spm'] for x in jog['samples']), 5)
            self.assertGreater(len({round(x['speed_m_s'], 3) for x in jog['samples']}), 20)
            for point in jog['samples']:
                self.assertAlmostEqual(point['stride_m'] * point['cadence_spm'] / 60,
                                       point['speed_m_s'], places=8)
            self.assertEqual(report, build_preview(meters, seconds, MotionOptions(seed=42)))

    def cli(self):
        spec = importlib.util.spec_from_file_location('motion_preview_entry',
            Path(__file__).with_name('preview_motion.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_cli_writes_self_contained_preview_without_clients_or_session(self):
        cli = self.cli()
        with tempfile.TemporaryDirectory() as folder, \
                patch('sport_client.SportClient792.__init__', side_effect=AssertionError('account forbidden')), \
                patch('login.client.LoginClient.__init__', side_effect=AssertionError('login forbidden')), \
                patch('core.local_state.load_session', side_effect=AssertionError('session forbidden')), \
                patch('urllib.request.urlopen', side_effect=AssertionError('HTTP forbidden')), \
                patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(cli.main(['--distance', '2537', '--time-sec', '1183',
                '--seed', '42', '--output-dir', folder]), 0)
            paths = json.loads(output.getvalue())
            report = json.loads(Path(paths['data']).read_text(encoding='utf-8'))
            html = Path(paths['html']).read_text(encoding='utf-8')
            self.assertTrue(report['all_checks_passed'])
            self.assertIn('离线模拟', html)
            self.assertIn('<svg', html)
            self.assertNotIn('https://', html)
            self.assertNotIn('http://', html)
            self.assertEqual(len(list(Path(folder).iterdir())), 2)

    def test_invalid_input_does_not_write_partial_preview(self):
        cli = self.cli()
        with tempfile.TemporaryDirectory() as folder, patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(cli.main(['--distance', '2537', '--time-sec', '30',
                                       '--output-dir', folder]), 2)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_untrusted_route_name_is_escaped_in_html(self):
        cli = self.cli()
        with tempfile.TemporaryDirectory() as folder, patch('sys.stdout', new_callable=io.StringIO) as output:
            route = Path(folder) / 'route.json'
            from test_loop_route import stadium
            route.write_text(json.dumps({'name': '<script>alert(1)</script>',
                'coord_type': 'bd09', 'loop': stadium()}), encoding='utf-8')
            self.assertEqual(cli.main(['--seed', '42', '--route-file', str(route),
                                       '--output-dir', str(Path(folder) / 'output')]), 0)
            html = Path(json.loads(output.getvalue())['html']).read_text(encoding='utf-8')
            self.assertNotIn('<script>', html)
            self.assertIn('&lt;script&gt;', html)


if __name__ == '__main__':
    unittest.main()
