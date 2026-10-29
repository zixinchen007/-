"""V3 preview is account-free and retains the original comparison models."""
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest
import test_motion_preview as preview_tests
from running.motion_v3 import MotionV3Options


class V3PreviewTests(unittest.TestCase):
    setUp = preview_tests.MotionPreviewTests.setUp
    cli = preview_tests.MotionPreviewTests.cli

    def test_cli_v3_with_and_without_variation(self):
        for variation in ([], ['--lap-variation']):
            with tempfile.TemporaryDirectory() as folder, patch('sys.stdout',new_callable=io.StringIO) as out, \
                    patch('core.local_state.load_session',side_effect=AssertionError('session forbidden')), \
                    patch('sport_client.SportClient792.__init__',side_effect=AssertionError('client forbidden')):
                self.assertEqual(self.cli().main(['--motion-model','v3','--seed','7',
                    '--output-dir',folder,*variation]),0)
                paths=json.loads(out.getvalue())
                report=json.loads(Path(paths['data']).read_text(encoding='utf-8'))
                self.assertEqual(set(report['models']),{'constant','jog','v3'})
                self.assertTrue(report['all_checks_passed'])
                v3=report['models']['v3']
                self.assertIsNone(v3['samples'][0]['pace_min_km'])
                self.assertEqual(v3['samples'][-1]['speed_m_s'],0)
                self.assertEqual(v3['map_points'][-1]['steps'],v3['summary']['total_steps'])
                html=Path(paths['html']).read_text(encoding='utf-8')
                self.assertIn('motion-v3',html)
                self.assertNotIn('NaN',html)
                self.assertNotIn('Infinity',html)
                if variation:
                    self.assertIn('geometry',v3)


if __name__ == '__main__':
    unittest.main()
