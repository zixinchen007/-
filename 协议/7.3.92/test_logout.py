"""Offline session invalidation and CLI cleanup regressions."""
import importlib.util
import io
from pathlib import Path
import socket
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from core import local_state
from sport_client import SportClient792


class LogoutTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.state_dir = Path(folder.name)
        state = patch.object(local_state, 'STATE_DIR', self.state_dir)
        state.start()
        self.addCleanup(state.stop)
        network = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        network.start()
        self.addCleanup(network.stop)
        local_state.save_session(123, 'TEST-SESSION-TOKEN', 456)
        self.client = SportClient792()
        self.device = (self.state_dir / 'device_id.txt').read_bytes()

    def test_confirmed_logout_clears_only_auth_and_preserves_device(self):
        replies = [{'error': 10000}, {'error': 'Unauthorized'}]
        with patch.object(self.client.profile, 'execute_request', side_effect=replies) as request:
            self.assertTrue(self.client.logout())
        self.assertEqual(request.call_args_list[0].args[0], 'POST')
        self.assertTrue(request.call_args_list[0].args[1].endswith('/api/v6/user/logout'))
        self.assertEqual(request.call_args_list[1].args[0], 'GET')
        self.assertIsNone(local_state.load_session())
        for client in (self.client, self.client.profile, self.client.policy,
                       self.client.running, self.client.history, self.client.ads, self.client.telemetry):
            self.assertEqual((client.uid, client.token, client.unid), (None, None, None))
        self.assertEqual((self.state_dir / 'device_id.txt').read_bytes(), self.device)

    def test_transport_errors_and_active_or_unknown_responses_preserve_session(self):
        for response in ({'error': -1}, {'error': 500}, {'error': 10000}, {}, None):
            with self.subTest(response=response), patch.object(
                    self.client.profile, 'execute_request', return_value=response):
                self.assertFalse(self.client.logout())
                self.assertEqual(local_state.load_session()['token'], 'TEST-SESSION-TOKEN')
                self.assertEqual(self.client.token, 'TEST-SESSION-TOKEN')

    def test_exception_preserves_session_and_a_later_attempt_can_confirm(self):
        with patch.object(self.client.profile, 'execute_request', side_effect=TimeoutError):
            self.assertFalse(self.client.logout())
        with patch.object(self.client.profile, 'execute_request',
                          side_effect=[{'error': 10000}, {'error': 401}]):
            self.assertTrue(self.client.logout())

    def test_explicit_session_logout_does_not_delete_another_cached_session(self):
        local_state.save_session(789, 'OTHER-TEST-SESSION', 456)
        with patch.object(self.client.profile, 'execute_request',
                          side_effect=[{'error': 10000}, {'error': 'UNAUTHORIZED'}]):
            self.assertTrue(self.client.logout())
        self.assertEqual(local_state.load_session()['token'], 'OTHER-TEST-SESSION')

    def test_local_cleanup_failure_is_not_reported_as_complete(self):
        with patch.object(self.client.profile, 'execute_request',
                          side_effect=[{'error': 10000}, {'error': 401}]), \
                patch('sport_client.clear_session', side_effect=OSError('test failure')):
            self.assertFalse(self.client.logout())
        self.assertEqual(self.client.token, 'TEST-SESSION-TOKEN')
        self.assertIsNotNone(local_state.load_session())

    def test_client_without_token_does_not_request_or_clear_unrelated_cache(self):
        self.client.token = None
        with patch.object(self.client.profile, 'execute_request') as request:
            self.assertTrue(self.client.logout())
        request.assert_not_called()
        self.assertIsNotNone(local_state.load_session())

    def test_concurrent_new_session_is_not_removed_by_old_logout(self):
        read_started, writer_done = threading.Event(), threading.Event()
        original_load = local_state.load_session

        def delayed_load():
            snapshot = original_load()
            read_started.set()
            writer_done.wait(.2)
            return snapshot

        def writer():
            if read_started.wait(2):
                local_state.save_session(789, 'NEW-TEST-SESSION', 456)
                writer_done.set()

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            with patch.object(local_state, 'load_session', side_effect=delayed_load):
                local_state.clear_session(123, 'TEST-SESSION-TOKEN')
        finally:
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(writer_done.is_set())
        self.assertEqual(original_load()['token'], 'NEW-TEST-SESSION')


class LogoutCliTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('logout_test_cli', Path(__file__).with_name('cli.py'))
        self.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cli)

    def invoke(self, args, client):
        with patch.object(self.cli, 'SportClient792', return_value=client), \
                patch.object(sys, 'argv', ['cli.py', *args]), \
                patch('sys.stdout', new_callable=io.StringIO) as output:
            code = self.cli.main()
        return code, output.getvalue()

    def run_client(self):
        client = Mock()
        client.token, client.uid, client.unid = 'TEST-PRIVATE-TOKEN', '123', '456'
        client.execute_run.return_value = SimpleNamespace(success=True, is_valid=True,
            rrid=1, total_distance=2500, total_time=1201, message='test accepted')
        client.logout.return_value = True
        return client

    def test_logout_without_cache_is_noop_without_constructing_client(self):
        with patch.object(self.cli, 'load_session', return_value=None), \
                patch.object(self.cli, 'SportClient792') as constructor, \
                patch.object(sys, 'argv', ['cli.py', '--logout']), \
                patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(self.cli.main(), 0)
        constructor.assert_not_called()

    def test_logout_is_standalone_and_other_actions_never_run(self):
        client = self.run_client()
        code, text = self.invoke(['--logout', '-t', 'TEST-PRIVATE-TOKEN', '--uid', '123'], client)
        self.assertEqual(code, 0)
        client.logout.assert_called_once()
        client.login.assert_not_called()
        client.sync_server_time.assert_not_called()
        client.execute_run.assert_not_called()
        self.assertNotIn('TEST-PRIVATE-TOKEN', text)

    def test_conflicting_actions_fail_before_client_construction(self):
        cases = (['--logout', '--run'], ['--logout', '--history'],
                 ['--logout', '-u', 'TEST-USER', '-p', 'TEST-PASSWORD'],
                 ['--logout-after-run'], ['--logout', '-t', 'TEST-TOKEN'])
        for args in cases:
            with self.subTest(args=args), patch.object(self.cli, 'SportClient792') as constructor, \
                    patch.object(sys, 'argv', ['cli.py', *args]), \
                    patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit):
                self.cli.main()
            constructor.assert_not_called()

    def test_run_cleanup_is_opt_in_and_runs_after_success(self):
        client = self.run_client()
        code, _ = self.invoke(['--run'], client)
        self.assertEqual(code, 0)
        client.logout.assert_not_called()
        code, _ = self.invoke(['--run', '--logout-after-run'], client)
        self.assertEqual(code, 0)
        client.logout.assert_called_once()

    def test_run_exception_still_attempts_logout(self):
        client = self.run_client()
        client.execute_run.side_effect = RuntimeError('test interrupted')
        with self.assertRaises(RuntimeError):
            self.invoke(['--run', '--logout-after-run'], client)
        client.logout.assert_called_once()

    def test_cleanup_failure_changes_exit_code_without_resubmission(self):
        client = self.run_client()
        client.logout.return_value = False
        code, text = self.invoke(['--run', '--logout-after-run'], client)
        self.assertEqual(code, 2)
        client.execute_run.assert_called_once()
        self.assertIn('退出未确认', text)
        self.assertNotIn(client.token, text)

    def test_login_success_does_not_print_raw_token(self):
        client = self.run_client()
        client.login.return_value = SimpleNamespace(success=True)
        code, text = self.invoke(['-u', 'TEST-USER', '-p', 'TEST-PASSWORD', '--run'], client)
        self.assertEqual(code, 0)
        self.assertNotIn(client.token, text)


if __name__ == '__main__':
    unittest.main()
