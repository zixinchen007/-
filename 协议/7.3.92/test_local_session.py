import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core import local_state
from core.config import load_customer_device
from sport_client import SportClient792


class LocalSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state_dir = Path(self.temporary.name)
        self.patch = patch.object(local_state, "STATE_DIR", self.state_dir)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_device_identity_survives_new_processes(self):
        code = (
            "from pathlib import Path; import sys; "
            "from core import local_state; local_state.STATE_DIR=Path(sys.argv[1]); "
            "from core.config import load_customer_device; "
            "print(load_customer_device()['DeviceId'])"
        )
        command = [sys.executable, "-c", code, str(self.state_dir)]
        options = {"cwd": Path(__file__).resolve().parent, "text": True}
        first = subprocess.check_output(command, **options).strip()
        second = subprocess.check_output(command, **options).strip()
        self.assertEqual(first, second)
        self.assertEqual(first, load_customer_device()["DeviceId"])

    def test_successful_login_is_reused_without_another_login(self):
        client = SportClient792()
        user = SimpleNamespace(uid=123, token="test-only-token", unid=456)
        result = SimpleNamespace(success=True, user=user)
        with patch.object(client.login_client, "login", return_value=result):
            client.login("test-user", "test-only-password")
        with patch("sport_client.LoginClient.login", side_effect=AssertionError("unexpected login")):
            reopened = SportClient792()
        self.assertEqual((reopened.uid, reopened.token, reopened.unid),
                         ("123", "test-only-token", "456"))
        self.assertEqual(reopened.device_config["DeviceId"], client.device_config["DeviceId"])
        self.assertEqual(reopened.profile.token, reopened.token)
        stored = json.loads((self.state_dir / "session.json").read_text(encoding="utf-8"))
        self.assertNotIn("password", stored)
        self.assertNotIn("test-only-password", json.dumps(stored))

    def test_failed_login_does_not_replace_a_working_session(self):
        local_state.save_session(123, "existing-test-token", 456)
        client = SportClient792()
        result = SimpleNamespace(success=False, user=None)
        with patch.object(client.login_client, "login", return_value=result):
            client.login("test-user", "wrong-test-password")
        self.assertEqual(local_state.load_session()["token"], "existing-test-token")

    def test_explicit_credentials_take_priority(self):
        local_state.save_session(123, "cached-test-token", 456)
        client = SportClient792(uid=789, token="explicit-test-token", unid=999)
        self.assertEqual((client.uid, client.token, client.unid),
                         ("789", "explicit-test-token", "999"))

    def test_missing_session_does_not_trigger_login(self):
        with patch("sport_client.LoginClient.login", side_effect=AssertionError("unexpected login")):
            client = SportClient792()
        self.assertIsNone(client.token)

    def test_corrupt_session_is_not_silently_replaced(self):
        path = self.state_dir / "session.json"
        path.write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "本地会话文件无效"):
            SportClient792()
        self.assertEqual(path.read_text(encoding="utf-8"), "{broken")


if __name__ == "__main__":
    unittest.main()
