"""Promotion defaults must reach the real payload while legacy stays explicit."""
import importlib.util
import io
from pathlib import Path
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from running.client import RunningClient
from running.motion import MotionOptions, build_timeline
from sport_client import SportClient792


class MotionDefaultsTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        guard.start()
        self.addCleanup(guard.stop)
        device = patch('core.config.get_device_id', return_value='1' * 32)
        device.start()
        self.addCleanup(device.stop)

    def client(self):
        client = RunningClient(uid=1, token='DUMMY', unid=2, device_config={'DeviceId': '1' * 32})
        client.profile.get_server_time = Mock(return_value=SimpleNamespace(
            server_timestamp_ms=1700001183000, offset_seconds=0))
        client.policy.get_run_policy = Mock(return_value=SimpleNamespace(min_distance=2000, raw={}))
        client.policy.get_geo_fence = Mock(return_value=[])
        client.telemetry.send_startup_pv = Mock()
        client.telemetry.send_page_exposure = Mock()
        client.submit_record = Mock(return_value={'error': -1, 'message': 'offline stop'})
        return client

    def test_default_payload_uses_jog_with_a_fresh_seed_per_run(self):
        client = self.client()
        messages = []
        with patch('running.motion.random.SystemRandom.randrange', side_effect=[42, 43]):
            for seed in (42, 43):
                result = client.execute_full_run(2537, 1183, sport_type=6,
                                                 progress_callback=messages.append)
                self.assertFalse(result.success)
                body = client.submit_record.call_args.args[0]
                expected = build_timeline(2537, 1183, MotionOptions(seed=seed))
                self.assertEqual(body['totalSteps'], expected.total_steps)
                self.assertEqual(body['avgStepFreq'], round(expected.total_steps * 60 / 1183))
                self.assertEqual(body['speed'], round(1183 / 60 / 2.537 * 1000))
                self.assertGreater(len({row['distance'] for row in body['speedPerTenSec']}), 10)
                self.assertTrue(any(f'seed={seed}' in message for message in messages))

    def test_explicit_none_keeps_legacy_step_generation(self):
        client = self.client()
        with patch('running.client.random.uniform', return_value=1.0), \
                patch('running.client.build_timeline', side_effect=AssertionError('legacy must not build motion')):
            client.execute_full_run(2537, 1183, sport_type=6, motion_options=None)
        body = client.submit_record.call_args.args[0]
        self.assertEqual(body['totalSteps'], int(2537 / .72))

    def test_facade_defaults_to_jog_and_preserves_explicit_legacy(self):
        facade = SportClient792(uid=1, token='DUMMY', unid=2, device_config={'DeviceId': '1' * 32})
        facade.running.execute_full_run = Mock()
        facade.execute_run()
        options = facade.running.execute_full_run.call_args.kwargs['motion_options']
        self.assertIsInstance(options, MotionOptions)
        self.assertIsNone(options.seed)
        facade.execute_run(motion_options=None)
        self.assertIsNone(facade.running.execute_full_run.call_args.kwargs['motion_options'])

    def cli(self):
        spec = importlib.util.spec_from_file_location('motion_defaults_cli', Path(__file__).with_name('cli.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_cli_defaults_seed_and_explicit_legacy(self):
        cli = self.cli()
        for flags, expected_seed, legacy in (([], None, False), (['--seed', '42'], 42, False),
                                              (['--motion-model', 'legacy'], None, True)):
            with self.subTest(flags=flags):
                client = Mock()
                client.execute_run.return_value = SimpleNamespace(success=False, error_code=-1,
                                                                  message='offline stop')
                with patch.object(cli, 'SportClient792', return_value=client), \
                        patch('sys.argv', ['cli.py', '--run', *flags]), \
                        patch('sys.stdout', new_callable=io.StringIO):
                    cli.main()
                options = client.execute_run.call_args.kwargs.get('motion_options')
                if legacy:
                    self.assertIsNone(options)
                else:
                    self.assertIsInstance(options, MotionOptions)
                    self.assertEqual(options.seed, expected_seed)

    def test_inapplicable_seed_stops_before_client_construction(self):
        cli = self.cli()
        for flags in (['--seed', '42'], ['--run', '--motion-model', 'legacy', '--seed', '42']):
            with self.subTest(flags=flags), patch.object(cli, 'SportClient792') as constructor, \
                    patch('sys.argv', ['cli.py', *flags]), patch('sys.stderr', new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as stopped:
                    cli.main()
                self.assertEqual(stopped.exception.code, 2)
                constructor.assert_not_called()


if __name__ == '__main__':
    unittest.main()
