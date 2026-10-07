"""Regression checks for the packaged marketplace and persistent hook manifests."""
import json
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest import mock

from windows import nerfed_launcher as launcher


class PackagedMarketplaceTests(unittest.TestCase):
    def test_marketplace_points_to_packaged_plugin_and_preserves_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "bundle/plugin/.codex-plugin/plugin.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text('{"name":"is-gpt-nerfed","hooks":{}}', encoding="utf-8")
            with mock.patch.dict(os.environ, {"PLUGIN_ROOT": "", "CODEX_HOME": str(root / "codex"),
                                               "NERFED_HOME": str(root / "ledger")}), \
                    mock.patch.object(launcher.sys, "frozen", True, create=True), \
                    mock.patch.object(launcher.sys, "_MEIPASS", str(root / "bundle"), create=True):
                target = launcher.bundled_plugin_root()
                market_path = root / "ledger/.agents/plugins/marketplace.json"
                market = json.loads(market_path.read_text(encoding="utf-8"))
                self.assertEqual(root / "ledger" / market["plugins"][0]["source"]["path"], target)
                installed = target / ".codex-plugin/plugin.json"
                installed.write_text('{"hooks":"rewritten for Windows"}', encoding="utf-8")
                launcher.bundled_plugin_root()
                self.assertEqual(json.loads(installed.read_text())["hooks"], "rewritten for Windows")
                manifest.write_text('{"name":"is-gpt-nerfed","version":"new"}', encoding="utf-8")
                launcher.bundled_plugin_root()
                self.assertEqual(json.loads(installed.read_text())["version"], "new")


@unittest.skipUnless(os.environ.get("NERFED_FROZEN_UI_TEST"), "No packaged UI supplied")
class FrozenWorkerTests(unittest.TestCase):
    def test_worker_inherited_pipes_and_marketplace(self):
        executable = os.environ["NERFED_FROZEN_UI_TEST"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {**os.environ, "CODEX_HOME": str(root / "codex"),
                   "NERFED_HOME": str(root / "ledger"), "NERFED_NO_UPDATE_CHECK": "1"}
            env.pop("PLUGIN_ROOT", None)
            env.pop("CODEX_THREAD_ID", None)
            env.pop("CODEX_SESSION_ID", None)
            result = subprocess.run([executable, "selftest"], capture_output=True,
                                    text=True, encoding="utf-8", env=env, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("fingerprint bank loaded", result.stdout)
            market = json.loads((root / "ledger/.agents/plugins/marketplace.json").read_text())
            self.assertEqual(market["name"], "is-gpt-nerfed")
            payload = {"hook_event_name": "SessionStart", "session_id": "packaged-pipe-test"}
            result = subprocess.run([executable, "hook"], input=json.dumps(payload),
                                    capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            events = (root / "ledger/events.jsonl").read_text(encoding="utf-8")
            self.assertIn("packaged-pipe-test", events)
