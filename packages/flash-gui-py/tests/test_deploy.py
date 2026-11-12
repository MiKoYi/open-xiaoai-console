"""部署核心逻辑测试：配置生成、校验、Docker 命令拼装、文档发现。

不需要 Docker 或 SSH，直接跑：
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deploy_core as dcore  # noqa: E402


class ValidateTests(unittest.TestCase):
    def test_xiaozhi_missing_keywords(self) -> None:
        errors = dcore.validate("xiaozhi", {"keywords": "", "ota_url": "https://a/", "ws_url": "wss://b/"})
        self.assertTrue(any("唤醒词" in item for item in errors))

    def test_xiaozhi_bad_url(self) -> None:
        errors = dcore.validate(
            "xiaozhi",
            {"keywords": "你好小智", "ota_url": "ftp://x", "ws_url": "wss://b/"},
        )
        self.assertTrue(any("http" in item for item in errors))

    def test_migpt_missing_key(self) -> None:
        errors = dcore.validate("migpt", {"api_key": "", "base_url": "https://x/v1", "model": "gpt"})
        self.assertTrue(any("API Key" in item for item in errors))

    def test_migpt_chat_completions_suffix(self) -> None:
        errors = dcore.validate(
            "migpt",
            {"api_key": "sk-1", "base_url": "https://x/v1/chat/completions", "model": "gpt"},
        )
        self.assertTrue(any("/chat/completions" in item for item in errors))

    def test_valid_values(self) -> None:
        self.assertEqual(
            dcore.validate(
                "xiaozhi",
                {"keywords": "你好小智", "ota_url": "https://x/ota", "ws_url": "wss://x/v1"},
            ),
            [],
        )


class RenderTests(unittest.TestCase):
    def test_xiaozhi_config(self) -> None:
        content = dcore.render_config(
            "xiaozhi",
            {
                "keywords": "你好小智,豆包豆包",
                "ota_url": "https://x/ota/",
                "ws_url": "wss://x/v1/",
            },
        )
        self.assertIn('"你好小智"', content)
        self.assertIn('"豆包豆包"', content)
        self.assertIn("APP_CONFIG", content)
        self.assertIn("https://x/ota/", content)

    def test_xiaozhi_escapes_quotes(self) -> None:
        content = dcore.render_config(
            "xiaozhi",
            {"keywords": 'say "hi"', "ota_url": "https://x/", "ws_url": "wss://x/"},
        )
        self.assertIn('\\"hi\\"', content)

    def test_migpt_config(self) -> None:
        content = dcore.render_config(
            "migpt",
            {"api_key": "sk-test", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "system_prompt": "你是助手"},
        )
        self.assertIn("sk-test", content)
        self.assertIn("deepseek-chat", content)
        self.assertIn("https://api.deepseek.com/v1", content)
        self.assertIn("你是助手", content)
        self.assertIn("kOpenXiaoAIConfig", content)

    def test_migpt_newline_escaped(self) -> None:
        content = dcore.render_config(
            "migpt",
            {"api_key": "k", "base_url": "https://x/v1", "model": "m", "system_prompt": "第一行\n第二行"},
        )
        self.assertIn("\\n", content)

    def test_xiaozhi_config_is_valid_python(self) -> None:
        content = dcore.render_config(
            "xiaozhi",
            {"keywords": "你好小智,hi siri", "ota_url": "https://x/ota/", "ws_url": "wss://x/v1/"},
        )
        compile(content, "config.py", "exec")
        namespace: dict = {}
        exec(content, namespace)  # noqa: S102 - 校验生成的配置能否正常执行
        self.assertEqual(namespace["APP_CONFIG"]["wakeup"]["keywords"], ["你好小智", "hi siri"])

    def test_write_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = dcore.write_config("xiaozhi", {"keywords": "a", "ota_url": "https://x/", "ws_url": "wss://x/"}, temp)
            self.assertTrue(path.is_file())
            self.assertEqual(path.name, "config.py")


class DockerCommandTests(unittest.TestCase):
    def test_run_args(self) -> None:
        args = dcore.docker_run_args("xiaozhi", r"D:\tmp\config.py", 4399)
        self.assertIn("run", args)
        self.assertIn("idootop/open-xiaoai-xiaozhi:latest", args)
        self.assertIn("4399:4399", args)
        self.assertIn(r"D:\tmp\config.py:/app/config.py", args)
        self.assertIn("open-xiaoai-xiaozhi", args)

    def test_run_args_custom_port(self) -> None:
        args = dcore.docker_run_args("migpt", "/tmp/config.ts", 5000)
        self.assertIn("5000:4399", args)

    def test_source_mode_rejected(self) -> None:
        with self.assertRaises(dcore.DeployError):
            dcore.docker_run_args("gemini", "/tmp/x")

    def test_connection_url(self) -> None:
        self.assertEqual(dcore.connection_url("192.168.1.10", 4399), "ws://192.168.1.10:4399")

    def test_remote_path(self) -> None:
        ssh = dcore.SshConfig(host="example.com", user="root")
        self.assertEqual(
            dcore.remote_config_path("xiaozhi", ssh),
            "/root/open-xiaoai/xiaozhi/config.py",
        )
        ssh2 = dcore.SshConfig(host="example.com", user="ubuntu")
        self.assertEqual(
            dcore.remote_config_path("migpt", ssh2),
            "/home/ubuntu/open-xiaoai/migpt/config.ts",
        )


class MemoryAndBuildTests(unittest.TestCase):
    def test_validate_memory(self) -> None:
        self.assertEqual(dcore.validate_memory(""), "")
        self.assertEqual(dcore.validate_memory("512m"), "")
        self.assertEqual(dcore.validate_memory("1g"), "")
        self.assertNotEqual(dcore.validate_memory("512"), "")
        self.assertNotEqual(dcore.validate_memory("abc"), "")
        self.assertNotEqual(dcore.validate_memory("0m"), "")

    def test_run_args_with_memory(self) -> None:
        args = dcore.docker_run_args("xiaozhi", "/tmp/config.py", 4399, memory="512m")
        self.assertIn("--memory", args)
        self.assertIn("512m", args)
        self.assertIn("--memory-swap", args)
        self.assertIn("1024m", args)

    def test_source_image_tag(self) -> None:
        self.assertEqual(dcore.source_image_tag("migpt"), "open-xiaoai-migpt:local")

    def test_docker_build_args(self) -> None:
        repo = Path("/tmp/open-xiaoai")
        args = dcore.docker_build_args("xiaozhi", repo)
        self.assertIn("-f", args)
        self.assertTrue(str(args[2]).replace("\\", "/").endswith("examples/xiaozhi/Dockerfile"))
        self.assertEqual(args[-1], str(repo))

    def test_deploy_local_rejects_bad_memory(self) -> None:
        values = {"keywords": "你好小智", "ota_url": "https://x/", "ws_url": "wss://x/"}
        with self.assertRaises(dcore.DeployError):
            dcore.deploy_local("xiaozhi", values, memory="abc")

    def test_deploy_remote_rejects_bad_memory(self) -> None:
        values = {"keywords": "你好小智", "ota_url": "https://x/", "ws_url": "wss://x/"}
        ssh = dcore.SshConfig(host="example.com")
        with self.assertRaises(dcore.DeployError):
            dcore.deploy_remote("xiaozhi", values, ssh, memory="512")


class MirrorAndUploadTests(unittest.TestCase):
    def test_auto_mirrors(self) -> None:
        candidates = dcore.mirror_candidates("auto")
        self.assertEqual(len(candidates), 3)
        self.assertEqual(candidates[0][1], "")
        self.assertTrue(candidates[1][1].startswith("https://"))

    def test_preset_mirror(self) -> None:
        candidates = dcore.mirror_candidates("ghfast")
        self.assertEqual(candidates[0][1], "https://ghfast.top/")
        self.assertTrue(dcore.tarball_url(candidates[0][1]).startswith("https://ghfast.top/https://codeload.github.com/"))

    def test_custom_mirror(self) -> None:
        self.assertEqual(dcore.mirror_candidates("custom", "https://ghfast.top")[0][1], "https://ghfast.top/")
        with self.assertRaises(dcore.DeployError):
            dcore.mirror_candidates("custom", "")

    def test_apply_image_prefix(self) -> None:
        image = "idootop/open-xiaoai-xiaozhi:latest"
        self.assertEqual(dcore.apply_image_prefix(image, ""), image)
        self.assertEqual(
            dcore.apply_image_prefix(image, "docker.m.daocloud.io"),
            "docker.m.daocloud.io/" + image,
        )
        self.assertEqual(
            dcore.apply_image_prefix(image, "docker.m.daocloud.io/"),
            "docker.m.daocloud.io/" + image,
        )

    def test_create_source_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "open-xiaoai"
            (repo / "examples" / "xiaozhi").mkdir(parents=True)
            (repo / "examples" / "xiaozhi" / "Dockerfile").write_text("FROM scratch", encoding="utf-8")
            (repo / ".git").mkdir()
            (repo / ".git" / "config").write_text("x", encoding="utf-8")
            (repo / "node_modules").mkdir()
            (repo / "node_modules" / "a.js").write_text("x", encoding="utf-8")

            archive = dcore.create_source_archive(repo)
            self.assertTrue(archive.is_file())
            with tarfile.open(archive, "r:gz") as tar:
                names = tar.getnames()
            self.assertTrue(any(name.endswith("examples/xiaozhi/Dockerfile") for name in names))
            self.assertFalse(any(".git" in name for name in names))
            self.assertFalse(any("node_modules" in name for name in names))
            archive.unlink()

    def test_fetch_source_bundle_uses_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = dcore.source_repo_dir(temp)
            (repo / "examples").mkdir(parents=True)
            self.assertEqual(dcore.fetch_source_bundle(temp), repo)

    def test_remote_extract_command(self) -> None:
        command = dcore.remote_extract_command("/root/open-xiaoai/src.tar.gz", "/root/open-xiaoai/src")
        self.assertIn("--strip-components=1", command)
        self.assertIn("/root/open-xiaoai/src.tar.gz", command)


class DiscoveryTests(unittest.TestCase):
    def test_examples_found(self) -> None:
        entries = dcore.discover_examples()
        keys = {entry.key for entry in entries}
        self.assertTrue({"xiaozhi", "migpt", "gemini"} <= keys)
        specs = {entry.key: entry.spec for entry in entries}
        self.assertIsNotNone(specs["xiaozhi"])
        self.assertTrue(specs["xiaozhi"].deployable)
        self.assertIsNotNone(specs["gemini"])
        self.assertFalse(specs["gemini"].deployable)
        if "kws" in keys:
            self.assertIsNone(specs["kws"])

    def test_client_commands(self) -> None:
        text = dcore.client_commands("ws://1.2.3.4:4399")
        self.assertIn("/data/open-xiaoai/server.txt", text)
        self.assertIn("ws://1.2.3.4:4399", text)
        self.assertIn("init.sh", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
