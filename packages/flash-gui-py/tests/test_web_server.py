"""Web 服务接口测试：不启动浏览器，直接请求本地 HTTP 服务。"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import web_server  # noqa: E402


class WebServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temp = tempfile.TemporaryDirectory()
        web_server.STATE_PATH = Path(cls._temp.name) / "state.json"
        cls.httpd, cls.hub = web_server.create_server(0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls._temp.cleanup()

    def get_json(self, path: str) -> dict:
        with urllib.request.urlopen(self.base + path, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_json(self, path: str, payload: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_index_page(self) -> None:
        with urllib.request.urlopen(self.base + "/", timeout=5) as response:
            html = response.read().decode("utf-8")
        self.assertIn("Open-XiaoAI", html)
        self.assertIn("刷机", html)

    def test_bootstrap_payload(self) -> None:
        data = self.get_json("/api/bootstrap")
        self.assertEqual([model["key"] for model in data["models"]], ["LX06", "OH2P"])
        self.assertIn("xiaozhi", [mode["key"] for mode in data["modes"]])
        self.assertTrue(data["mirrors"])

    def test_status_payload(self) -> None:
        data = self.get_json("/api/status")
        self.assertIn("service", data)
        self.assertIn("speaker", data)

    def test_settings_saved(self) -> None:
        status, data = self.post_json("/api/settings", {"speaker_ip": "127.0.0.1"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(self.hub.state["speaker_ip"], "127.0.0.1")

    def test_unknown_action_rejected(self) -> None:
        status, data = self.post_json("/api/start", {"action": "nope"})
        self.assertEqual(status, 409)
        self.assertFalse(data["ok"])

    def test_model_action_returns_config_path(self) -> None:
        values = {"api_key": "k", "base_url": "https://x/v1", "model": "m", "system_prompt": "hi"}
        with mock.patch.object(
            web_server.dcore,
            "save_model_config",
            return_value={"path": "/opt/open-xiaoai/migpt/config.ts", "container": "c", "restarted": True},
        ):
            result = json.loads(
                self.hub._do_model({"mode": "migpt", "target": "local", "values": values})
            )
        self.assertEqual(result["path"], "/opt/open-xiaoai/migpt/config.ts")
        self.assertTrue(result["restarted"])

    def test_model_restart_action(self) -> None:
        with mock.patch.object(
            web_server.dcore,
            "restart_container",
            return_value={"container": "open-xiaoai-migpt", "restarted": True},
        ):
            result = json.loads(self.hub._do_model_restart({"mode": "migpt", "target": "local"}))
        self.assertTrue(result["restarted"])

    def test_xp_status_action(self) -> None:
        with mock.patch.object(
            web_server.devcore,
            "xp_status",
            return_value={"installed": True, "running": True, "values": {}},
        ):
            result = json.loads(self.hub._do_xp_status({"ssh": {"host": "192.168.31.201"}}))
        self.assertTrue(result["installed"])
        self.assertTrue(result["running"])

    def test_hm_status_action(self) -> None:
        with (
            mock.patch.object(
                web_server.devcore,
                "hm_status",
                return_value={"state": "running", "running": True, "logs": "", "url": "http://1.2.3.4:8930"},
            ),
            mock.patch.object(web_server.devcore, "hm_check", return_value=True),
        ):
            result = json.loads(self.hub._do_hm_status({"ssh": {"host": "1.2.3.4"}}))
        self.assertEqual(result["state"], "running")
        self.assertTrue(result["reachable"])

    def test_busy_action_rejected(self) -> None:
        self.hub.busy = True
        try:
            status, data = self.post_json("/api/start", {"action": "find_exe"})
            self.assertEqual(status, 409)
            self.assertIn("正在执行", data["message"])
        finally:
            self.hub.busy = False


if __name__ == "__main__":
    unittest.main(verbosity=2)
