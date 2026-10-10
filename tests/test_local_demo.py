import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import local_demo


class LocalDemoTests(unittest.TestCase):
    def test_unrelated_container_cannot_be_changed(self):
        result = SimpleNamespace(returncode=0, stdout=json.dumps([{"Config": {"Labels": {}}}]))
        with patch.object(local_demo, "docker", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "unrelated container"):
                local_demo.owned_container(local_demo.APP)

    def test_missing_container_is_not_owned(self):
        with patch.object(local_demo, "docker", return_value=SimpleNamespace(returncode=1)):
            self.assertFalse(local_demo.owned_container(local_demo.APP))

    def test_stop_keeps_budget_and_removes_local_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            state = directory / "state.json"
            state.write_text(json.dumps({"running": True}))
            env = directory / "backend.env"
            env.write_text("SARVAM_API_KEY=offline-test\n")
            invite = directory / "PRIVATE-INVITE.md"
            invite.write_text("offline-test")
            with patch.multiple(local_demo, DIRECTORY=directory, STATE=state, ENV_FILE=env), \
                    patch.object(local_demo, "owned_container", return_value=True), \
                    patch.object(local_demo, "docker") as docker:
                local_demo.stop()
            self.assertFalse(env.exists())
            self.assertFalse(invite.exists())
            self.assertFalse(json.loads(state.read_text())["running"])
            self.assertEqual([call.args for call in docker.call_args_list], [
                ("stop", local_demo.TUNNEL), ("rm", local_demo.TUNNEL),
                ("stop", local_demo.APP), ("rm", local_demo.APP)])
