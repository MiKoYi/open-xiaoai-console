"""部署核心逻辑测试：配置生成、校验、Docker 命令拼装、文档发现。

不需要 Docker 或 SSH，直接跑：
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import hashlib
import io
import os
import shlex
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deploy_core as dcore  # noqa: E402
import device_core as devcore  # noqa: E402


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

    def test_unknown_mode_rejected(self) -> None:
        with self.assertRaises(dcore.DeployError):
            dcore.docker_run_args("unknown", "/tmp/x")

    def test_connection_url(self) -> None:
        self.assertEqual(dcore.connection_url("192.168.1.10", 4399), "ws://192.168.1.10:4399")

    def test_remote_path(self) -> None:
        ssh = dcore.SshConfig(host="example.com", user="root")
        self.assertEqual(
            dcore.remote_config_path("xiaozhi", ssh),
            "/opt/open-xiaoai/xiaozhi/config.py",
        )
        ssh2 = dcore.SshConfig(host="example.com", user="ubuntu")
        self.assertEqual(
            dcore.remote_config_path("migpt", ssh2),
            "/home/ubuntu/open-xiaoai/migpt/config.ts",
        )

    def test_migpt_defaults_to_doubao(self) -> None:
        fields = {field.key: field for field in dcore.MIGPT.fields}
        self.assertEqual(fields["model"].default, "doubao-seed-1-6-250615")
        self.assertEqual(fields["base_url"].default, "https://ark.cn-beijing.volces.com/api/v3")
        self.assertIn("doubao-seed-1-6-250615", fields["model"].options)


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

    def test_source_run_args_limits_node_heap(self) -> None:
        args = dcore.docker_run_args(
            "migpt", "/tmp/config.ts", 4399, memory="256m", source_build=True
        )
        self.assertIn("--memory", args)
        self.assertIn("256m", args)
        self.assertIn("NODE_OPTIONS=--max-old-space-size=128 --max-semi-space-size=8", args)

    def test_lean_dockerfile_preferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            lean = repo / "examples" / "migpt" / "Dockerfile.lean"
            lean.parent.mkdir(parents=True)
            lean.write_text("FROM node:22-slim", encoding="utf-8")
            args = dcore.docker_build_args("migpt", repo)
            self.assertTrue(str(args[2]).replace("\\", "/").endswith("examples/migpt/Dockerfile.lean"))

    def test_source_image_tag(self) -> None:
        self.assertEqual(dcore.source_image_tag("migpt"), "open-xiaoai-migpt:local")

    def test_docker_build_args(self) -> None:
        repo = Path("/tmp/open-xiaoai")
        args = dcore.docker_build_args("xiaozhi", repo)
        self.assertIn("-f", args)
        self.assertTrue(str(args[2]).replace("\\", "/").endswith("examples/xiaozhi/Dockerfile"))
        self.assertEqual(args[-1], str(repo))

    def test_docker_build_args_with_proxy(self) -> None:
        repo = Path("/tmp/open-xiaoai")
        args = dcore.docker_build_args("migpt", repo, "http://127.0.0.1:7890")
        self.assertIn("--add-host", args)
        self.assertIn("host.docker.internal:host-gateway", args)
        self.assertIn("HTTPS_PROXY=http://host.docker.internal:7890", args)
        self.assertIn("NO_PROXY=localhost,127.0.0.1,host.docker.internal", args)
        self.assertEqual(args[-1], str(repo))

    def test_docker_build_args_keeps_lan_proxy(self) -> None:
        args = dcore.docker_build_args("migpt", "/tmp/open-xiaoai", "http://192.168.1.2:7890")
        self.assertIn("HTTPS_PROXY=http://192.168.1.2:7890", args)

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
            (repo / ".source-complete").write_text("ok", encoding="utf-8")

            archive = dcore.create_source_archive(repo)
            self.assertTrue(archive.is_file())
            with tarfile.open(archive, "r:gz") as tar:
                names = tar.getnames()
            # 平铺打包：没有顶层目录，服务器上解压不需要 --strip-components
            self.assertIn("examples/xiaozhi/Dockerfile", names)
            self.assertFalse(any(name.startswith("open-xiaoai/") for name in names))
            self.assertFalse(any(".git" in name for name in names))
            self.assertFalse(any("node_modules" in name for name in names))
            self.assertFalse(any(".source-complete" in name for name in names))
            archive.unlink()

    def test_fetch_source_bundle_uses_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = dcore.source_repo_dir(temp)
            (repo / "examples" / "xiaozhi").mkdir(parents=True)
            (repo / "examples" / "xiaozhi" / "Dockerfile").write_text("FROM scratch", encoding="utf-8")
            self.assertEqual(dcore.fetch_source_bundle(temp), repo)

    def test_source_cache_requires_complete_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = dcore.source_repo_dir(temp)
            (repo / "examples").mkdir(parents=True)
            self.assertFalse(dcore._source_cache_ready(repo))
            (repo / "examples" / "xiaozhi").mkdir()
            (repo / "examples" / "xiaozhi" / "Dockerfile").write_text("FROM scratch", encoding="utf-8")
            self.assertTrue(dcore._source_cache_ready(repo))
            (repo / ".source-complete").write_text("ok", encoding="utf-8")
            self.assertTrue(dcore._source_cache_ready(repo))

    def test_source_archive_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "open-xiaoai"
            (repo / "examples").mkdir(parents=True)
            (repo / "examples" / "a.txt").write_text("x", encoding="utf-8")
            first = dcore.create_source_archive(repo, Path(temp) / "a.tar.gz")
            second = dcore.create_source_archive(repo, Path(temp) / "b.tar.gz")
            self.assertEqual(
                hashlib.sha256(first.read_bytes()).hexdigest(),
                hashlib.sha256(second.read_bytes()).hexdigest(),
            )
            # gzip 头里的 mtime 固定为 0，隔多久打包 sha256 都一样
            self.assertEqual(first.read_bytes()[4:8], b"\x00\x00\x00\x00")

    def test_prepare_source_tree_keeps_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            dcore._prepare_source_tree("migpt", temp)
            target = Path(temp) / "examples" / "migpt" / "Dockerfile.lean"
            self.assertTrue(target.is_file())
            stamp = target.stat().st_mtime_ns
            time.sleep(0.02)
            dcore._prepare_source_tree("migpt", temp)
            self.assertEqual(target.stat().st_mtime_ns, stamp)

    def test_remote_extract_command(self) -> None:
        command = dcore.remote_extract_command("/root/open-xiaoai/src.tar.gz", "/root/open-xiaoai/src")
        self.assertNotIn("--strip-components", command)
        self.assertIn("-C /root/open-xiaoai/src", command)
        self.assertIn("/root/open-xiaoai/src.tar.gz", command)
        self.assertNotIn("rm -f", command)  # 源码包留着，下次比对 sha256 后可以跳过上传


class DiscoveryTests(unittest.TestCase):
    def test_examples_found(self) -> None:
        entries = dcore.discover_examples()
        keys = {entry.key for entry in entries}
        self.assertTrue({"xiaozhi", "migpt"} <= keys)
        specs = {entry.key: entry.spec for entry in entries}
        self.assertIsNotNone(specs["xiaozhi"])
        self.assertTrue(specs["xiaozhi"].deployable)
        self.assertIsNotNone(specs["migpt"])
        self.assertTrue(specs["migpt"].deployable)
        if "kws" in keys:
            self.assertIsNone(specs["kws"])

    def test_client_commands(self) -> None:
        text = dcore.client_commands("ws://1.2.3.4:4399")
        self.assertIn("/data/open-xiaoai/server.txt", text)
        self.assertIn("ws://1.2.3.4:4399", text)
        self.assertIn("init.sh", text)


class _FakeOutput:
    """仿 paramiko 的 stdout/stderr：既能逐行迭代（_ssh_run），也能按字节读（下载）。"""

    def __init__(self, data: bytes = b"", exit_code: int = 0) -> None:
        self._data = data
        self.exit_code = exit_code
        self.channel = self

    def recv_exit_status(self) -> int:
        return self.exit_code

    def shutdown_write(self) -> None:
        pass

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk, self._data = self._data, b""
            return chunk
        chunk, self._data = self._data[:size], self._data[size:]
        return chunk

    def __iter__(self):
        for line in self._data.decode("utf-8").splitlines(True):
            yield line


class _FakeStdin:
    def __init__(self, sink: bytearray) -> None:
        self._sink = sink
        self.channel = _FakeOutput()

    def write(self, data: bytes) -> None:
        self._sink.extend(data)

    def flush(self) -> None:
        pass


class _FakeSftpFile:
    def __init__(self, client: "FakeSshClient", path: str) -> None:
        self._client = client
        self._path = path

    def write(self, text: str) -> None:
        self._client.files[self._path] = bytearray(text.encode("utf-8"))

    def __enter__(self) -> "_FakeSftpFile":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


class _FakeSftp:
    def __init__(self, client: "FakeSshClient") -> None:
        self._client = client

    def put(self, local: str, remote: str, callback=None) -> None:
        data = Path(local).read_bytes()
        self._client.files[remote] = bytearray(data)
        if callback:
            callback(len(data), len(data))

    def get(self, remote: str, local: str) -> None:
        Path(local).write_bytes(bytes(self._client.files[remote]))

    def file(self, remote: str, mode: str) -> _FakeSftpFile:
        return _FakeSftpFile(self._client, remote)

    def close(self) -> None:
        pass


class FakeSshClient:
    """只实现部署用到的 SSH 接口，'cat > 文件' 会写进 self.files。"""

    def __init__(
        self,
        sftp_raises: Exception | None = None,
        exit_code: int = 0,
        stderr: bytes = b"",
        outputs: dict[str, bytes | tuple[bytes, int]] | None = None,
    ) -> None:
        self.files: dict[str, bytearray] = {}
        self.commands: list[str] = []
        self.sftp_raises = sftp_raises
        self.open_sftp_calls = 0
        self.exit_code = exit_code
        self.stderr = stderr
        self.outputs = outputs or {}

    def close(self) -> None:
        pass

    def open_sftp(self):
        self.open_sftp_calls += 1
        if self.sftp_raises is not None:
            raise self.sftp_raises
        return _FakeSftp(self)

    def exec_command(self, command: str, get_pty: bool = False):
        self.commands.append(command)
        command = command.removeprefix("exec 2>&1; ")
        if command.startswith("cat > "):
            path = shlex.split(command[len("cat > "):])[0]
            sink = bytearray()
            self.files[path] = sink
            return _FakeStdin(sink), _FakeOutput(b"", self.exit_code), _FakeOutput(self.stderr)
        if command.startswith("sha256sum "):
            path = shlex.split(command[len("sha256sum "):])[0]
            data = self.files.get(path)
            if data is None:
                return _FakeStdin(bytearray()), _FakeOutput(b"", 1), _FakeOutput(b"")
            digest = hashlib.sha256(bytes(data)).hexdigest()
            return _FakeStdin(bytearray()), _FakeOutput(f"{digest}  {path}\n".encode()), _FakeOutput(b"")
        if command.startswith("cat "):
            path = shlex.split(command[len("cat "):])[0]
            data = bytes(self.files.get(path, b""))
            return _FakeStdin(bytearray()), _FakeOutput(data), _FakeOutput(b"")
        for marker, output in self.outputs.items():
            if marker in command:
                if isinstance(output, tuple):
                    data, code = output
                else:
                    data, code = output, self.exit_code
                return _FakeStdin(bytearray()), _FakeOutput(data, code), _FakeOutput(b"")
        return _FakeStdin(bytearray()), _FakeOutput(b"", self.exit_code), _FakeOutput(self.stderr)


class RemoteTransferTests(unittest.TestCase):
    def test_upload_text_falls_back_to_cat(self) -> None:
        client = FakeSshClient(sftp_raises=EOFError("EOF during negotiation"))
        logs: list[tuple[str, str, str]] = []
        dcore._upload_text(
            client,
            "/root/open-xiaoai/migpt/config.ts",
            "export default {}",
            lambda kind, text, level: logs.append((kind, text, level)),
        )
        self.assertEqual(bytes(client.files["/root/open-xiaoai/migpt/config.ts"]), b"export default {}")
        self.assertTrue(any("SFTP" in text for _, text, _ in logs))
        self.assertEqual(client.open_sftp_calls, 1)

    def test_upload_file_falls_back_to_cat(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "src.tar.gz"
            source.write_bytes(b"x" * (dcore.UPLOAD_CHUNK * 2 + 10))
            client = FakeSshClient(sftp_raises=RuntimeError("sftp disabled by server"))
            progress: list[tuple[int, int]] = []
            dcore._upload_file(
                client,
                source,
                "/root/open-xiaoai/src.tar.gz",
                callback=lambda sent, total: progress.append((sent, total)),
            )
            self.assertEqual(bytes(client.files["/root/open-xiaoai/src.tar.gz"]), source.read_bytes())
            self.assertEqual(progress[-1], (source.stat().st_size, source.stat().st_size))

    def test_upload_uses_sftp_when_available(self) -> None:
        client = FakeSshClient()
        dcore._upload_text(client, "/root/open-xiaoai/xiaozhi/config.py", "APP_CONFIG = {}")
        self.assertEqual(bytes(client.files["/root/open-xiaoai/xiaozhi/config.py"]), b"APP_CONFIG = {}")
        self.assertEqual(client.commands, [])

    def test_download_file_falls_back_to_cat(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "LX06_patched.squashfs"
            client = FakeSshClient(sftp_raises=EOFError("EOF during negotiation"))
            client.files["/root/open-xiaoai/patch/out/LX06_patched.squashfs"] = bytearray(b"firmware")
            dcore._download_file(client, "/root/open-xiaoai/patch/out/LX06_patched.squashfs", target)
            self.assertEqual(target.read_bytes(), b"firmware")

    def test_upload_stream_reports_cat_failure(self) -> None:
        client = FakeSshClient(exit_code=1, stderr=b"cat: write error: No space left on device")
        with self.assertRaises(dcore.DeployError) as ctx:
            dcore._upload_stream(client, "/root/open-xiaoai/src.tar.gz", [b"data"], 4)
        self.assertIn("No space left on device", str(ctx.exception))

    def test_ssh_run_wraps_connection_loss(self) -> None:
        class BrokenClient:
            def exec_command(self, *args, **kwargs):
                raise EOFError("Socket is closed")

        with self.assertRaises(dcore.DeployError) as ctx:
            dcore._ssh_run(BrokenClient(), "docker ps")
        self.assertIn("SSH 连接中断", str(ctx.exception))
        self.assertIn("swap", str(ctx.exception))

    def test_ssh_run_merges_stderr_for_whole_command(self) -> None:
        client = FakeSshClient(outputs={"echo hi": b"hi\n"})
        code, output = dcore._ssh_run(client, "echo hi")
        self.assertEqual(code, 0)
        self.assertEqual(client.commands[0], "exec 2>&1; echo hi")
        self.assertEqual(output.strip(), "hi")

    def test_ssh_run_reports_missing_exit_status(self) -> None:
        client = FakeSshClient(exit_code=-1)
        with self.assertRaises(dcore.DeployError) as ctx:
            dcore._ssh_run(client, "docker ps")
        self.assertIn("SSH 会话中断", str(ctx.exception))

    def test_deploy_remote_source_uses_flat_archive(self) -> None:
        """回归：源码包平铺上传，服务器解压不带 --strip-components。"""
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "open-xiaoai"
            (repo / "examples" / "migpt").mkdir(parents=True)
            (repo / "examples" / "migpt" / "Dockerfile").write_text("FROM scratch", encoding="utf-8")
            client = FakeSshClient(
                sftp_raises=EOFError("EOF during negotiation"),
                outputs={"/proc/meminfo": b"aarch64\n0.9", "docker ps": b"Up 5 seconds"},
            )
            values = {"api_key": "sk-1", "base_url": "https://x/v1", "model": "gpt"}
            with (
                mock.patch.object(dcore, "_ssh_connect", return_value=client),
                mock.patch.object(dcore, "fetch_source_bundle", return_value=repo),
                mock.patch.object(dcore, "_prepare_source_tree"),
            ):
                result = dcore.deploy_remote(
                    "migpt", values, dcore.SshConfig(host="example.com"), method="source"
                )

            self.assertEqual(result.url, "ws://example.com:4399")
            archive = bytes(client.files["/root/open-xiaoai/src.tar.gz"])
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
                names = tar.getnames()
            self.assertIn("examples/migpt/Dockerfile", names)
            self.assertTrue(
                any(
                    command.startswith("exec 2>&1; rm -rf /root/open-xiaoai/src")
                    for command in client.commands
                )
            )
            self.assertFalse(any("--strip-components" in command for command in client.commands))

    def test_deploy_remote_source_skips_completed_steps(self) -> None:
        """回归：服务器上已经是同一份源码时，上传 / 解压 / 构建都跳过。"""
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "open-xiaoai"
            archive = Path(temp) / "open-xiaoai-src.tar.gz"
            archive.write_bytes(b"fake source archive")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            logs: list[str] = []
            client = FakeSshClient(
                sftp_raises=EOFError("EOF during negotiation"),
                outputs={
                    "/proc/meminfo": b"aarch64\n0.9",
                    "docker ps": b"Up 5 seconds",
                    "docker image inspect": b"sha256:abc\n",
                },
            )
            client.files["/root/open-xiaoai/src.tar.gz"] = bytearray(archive.read_bytes())
            client.files["/root/open-xiaoai/src/.open-xiaoai-src"] = bytearray(digest.encode())
            client.files["/root/open-xiaoai/src/.open-xiaoai-built"] = bytearray(digest.encode())

            values = {"api_key": "sk-1", "base_url": "https://x/v1", "model": "gpt"}
            with (
                mock.patch.object(dcore, "_ssh_connect", return_value=client),
                mock.patch.object(dcore, "fetch_source_bundle", return_value=repo),
                mock.patch.object(dcore, "_prepare_source_tree"),
                mock.patch.object(dcore, "create_source_archive", return_value=archive),
            ):
                result = dcore.deploy_remote(
                    "migpt",
                    values,
                    dcore.SshConfig(host="example.com"),
                    method="source",
                    notify=lambda kind, text, level="": logs.append(text),
                )

            self.assertEqual(result.url, "ws://example.com:4399")
            self.assertFalse(any("src.tar.gz" in command and command.startswith("cat > ") for command in client.commands))
            self.assertFalse(any("tar -xzf" in command for command in client.commands))
            self.assertFalse(any("docker build" in command for command in client.commands))
            self.assertTrue(any("跳过上传" in text for text in logs))
            self.assertTrue(any("跳过解压" in text for text in logs))
            self.assertTrue(any("跳过构建" in text for text in logs))

    def test_deploy_remote_survives_missing_sftp(self) -> None:
        """回归：服务器禁用了 sftp 子系统时，部署应改用 cat 上传配置并继续。"""
        client = FakeSshClient(
            sftp_raises=EOFError("EOF during negotiation"),
            outputs={"/proc/meminfo": b"aarch64\n0.9", "docker ps": b"Up 5 seconds"},
        )
        ssh = dcore.SshConfig(host="example.com")
        values = {"keywords": "你好小智", "ota_url": "https://x/ota/", "ws_url": "wss://x/v1/"}
        with mock.patch.object(dcore, "_ssh_connect", return_value=client):
            result = dcore.deploy_remote("xiaozhi", values, ssh, method="image")
        self.assertEqual(result.url, "ws://example.com:4399")
        config = bytes(client.files["/opt/open-xiaoai/xiaozhi/config.py"])
        self.assertIn(b"APP_CONFIG", config)


class BuildErrorTests(unittest.TestCase):
    def test_registry_timeout_mentions_mirror(self) -> None:
        message = dcore._build_error_message(
            'Get "https://registry-1.docker.io/v2/": context deadline exceeded '
            "(Client.Timeout exceeded while awaiting headers)",
            1,
        )
        self.assertIn("镜像加速", message)
        self.assertIn("daemon.json", message)
        self.assertNotIn("构建进程被系统杀掉", message)

    def test_apt_missing_package_explains_container_proxy(self) -> None:
        message = dcore._build_error_message(
            "Package build-essential is not available, but is referred to by another package.\n"
            "This may mean that the package is missing, has been obsoleted, or is only available from another source",
            100,
        )
        self.assertIn("172.17.0.1", message)
        self.assertIn("host.docker.internal", message)
        self.assertIn("apt-get update", message)

    def test_apt_network_failure_mentions_proxy(self) -> None:
        message = dcore._build_error_message(
            "E: Failed to fetch http://deb.debian.org/debian/dists/bookworm/InRelease  "
            "Could not resolve 'deb.debian.org'",
            100,
        )
        self.assertIn("构建代理", message)
        self.assertIn("daemon.json", message)
        self.assertNotIn("建议先加 swap", message)

    def test_disk_full_mentions_cleanup(self) -> None:
        message = dcore._build_error_message("write /var/lib/docker: no space left on device", 1)
        self.assertIn("磁盘空间不足", message)

    def test_out_of_memory_mentions_swap(self) -> None:
        message = dcore._build_error_message("Killed", 137)
        self.assertIn("swap", message)

    def test_deploy_remote_reports_registry_timeout_on_pull(self) -> None:
        client = FakeSshClient(
            sftp_raises=EOFError("EOF during negotiation"),
            outputs={
                "/proc/meminfo": b"aarch64\n0.9",
                "docker pull": (b'Get "https://registry-1.docker.io/v2/": context deadline exceeded\n', 1),
            },
        )
        values = {"keywords": "你好小智", "ota_url": "https://x/ota/", "ws_url": "wss://x/v1/"}
        with mock.patch.object(dcore, "_ssh_connect", return_value=client):
            with self.assertRaises(dcore.DeployError) as ctx:
                dcore.deploy_remote("xiaozhi", values, dcore.SshConfig(host="example.com"))
        self.assertIn("镜像加速", str(ctx.exception))

    def test_build_remote_image_reports_registry_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "open-xiaoai"
            (repo / "examples" / "migpt").mkdir(parents=True)
            (repo / "examples" / "migpt" / "Dockerfile").write_text(
                "FROM node:22-bookworm\n", encoding="utf-8"
            )
            client = FakeSshClient(
                sftp_raises=EOFError("EOF during negotiation"),
                outputs={
                    "docker build": (
                        b'Get "https://registry-1.docker.io/v2/": context deadline exceeded\n',
                        1,
                    )
                },
            )
            with self.assertRaises(dcore.DeployError) as ctx:
                dcore.build_remote_image(
                    client,
                    "migpt",
                    dcore.SshConfig(host="example.com"),
                    repo,
                    build_proxy="http://127.0.0.1:7890",
                )
            message = str(ctx.exception)
            self.assertIn("镜像加速", message)
            self.assertNotIn("建议先加 swap", message)
            self.assertTrue(
                any("HTTPS_PROXY=http://host.docker.internal:7890" in command for command in client.commands)
            )


class RemoteSwapTests(unittest.TestCase):
    def test_detect_remote_env_reads_swap(self) -> None:
        client = FakeSshClient(
            outputs={"/proc/meminfo": b"aarch64\nMemTotal: 921600 kB\nSwapTotal: 0 kB\n"}
        )
        env = dcore.detect_remote_env(client)
        self.assertEqual(env[0], "aarch64")
        self.assertAlmostEqual(env[1], 0.9, places=1)
        self.assertEqual(env[2], 0.0)

    def test_ensure_remote_swap_creates_swapfile(self) -> None:
        client = FakeSshClient(outputs={"SwapTotal": b"2.0"})
        self.assertTrue(dcore.ensure_remote_swap(client, 0.0))
        commands = "\n".join(client.commands)
        self.assertIn("mkswap /swapfile", commands)
        self.assertIn("swapon /swapfile", commands)
        self.assertIn("/etc/fstab", commands)

    def test_ensure_remote_swap_skips_when_enabled(self) -> None:
        client = FakeSshClient()
        self.assertTrue(dcore.ensure_remote_swap(client, 1.0))
        self.assertEqual(client.commands, [])

    def test_ensure_remote_swap_reports_failure(self) -> None:
        client = FakeSshClient(exit_code=1)
        logs: list[str] = []
        self.assertFalse(
            dcore.ensure_remote_swap(
                client, 0.0, lambda kind, text, level="": logs.append(text)
            )
        )
        self.assertTrue(any("创建 swap 失败" in text for text in logs))

    def test_deploy_remote_adds_swap_when_low_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "open-xiaoai-src.tar.gz"
            archive.write_bytes(b"fake source archive")
            client = FakeSshClient(
                sftp_raises=EOFError("EOF during negotiation"),
                outputs={
                    "/proc/meminfo": b"aarch64\nMemTotal: 921600 kB\nSwapTotal: 0 kB\n",
                    "SwapTotal": b"2.0",
                    "docker ps": b"Up 5 seconds",
                },
            )
            values = {"api_key": "sk-1", "base_url": "https://x/v1", "model": "gpt"}
            with (
                mock.patch.object(dcore, "_ssh_connect", return_value=client),
                mock.patch.object(dcore, "fetch_source_bundle", return_value=Path(temp) / "repo"),
                mock.patch.object(dcore, "_prepare_source_tree"),
                mock.patch.object(dcore, "create_source_archive", return_value=archive),
            ):
                result = dcore.deploy_remote(
                    "migpt", values, dcore.SshConfig(host="example.com"), method="source"
                )

            self.assertEqual(result.url, "ws://example.com:4399")
            self.assertTrue(any("mkswap /swapfile" in command for command in client.commands))


class ModelConfigTests(unittest.TestCase):
    def test_mount_source_finds_config_path(self) -> None:
        text = (
            '[{"Type": "bind", "Source": "/opt/open-xiaoai/migpt/config.ts", '
            '"Destination": "/app/config.ts"}]'
        )
        self.assertEqual(dcore._mount_source(text, "migpt"), "/opt/open-xiaoai/migpt/config.ts")

    def test_mount_source_ignores_other_mounts(self) -> None:
        self.assertEqual(dcore._mount_source('[{"Source": "/tmp/x", "Destination": "/app/other"}]', "migpt"), "")
        self.assertEqual(dcore._mount_source("not json", "migpt"), "")

    def test_save_model_config_remote(self) -> None:
        client = FakeSshClient(
            sftp_raises=EOFError("EOF during negotiation"),
            outputs={
                "docker inspect": (
                    b'[{"Source": "/opt/open-xiaoai/migpt/config.ts",'
                    b' "Destination": "/app/config.ts"}]'
                )
            },
        )
        values = {"api_key": "sk-1", "base_url": "https://x/v1", "model": "m", "system_prompt": "你好"}
        with mock.patch.object(dcore, "_ssh_connect", return_value=client):
            result = dcore.save_model_config(
                "migpt", values, target="remote", ssh=dcore.SshConfig(host="example.com")
            )

        self.assertEqual(result["path"], "/opt/open-xiaoai/migpt/config.ts")
        self.assertTrue(result["restarted"])
        config = bytes(client.files["/opt/open-xiaoai/migpt/config.ts"]).decode("utf-8")
        self.assertIn("sk-1", config)
        self.assertIn("你好", config)
        self.assertTrue(any("docker restart open-xiaoai-migpt" in command for command in client.commands))

    def test_save_model_config_local(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "config.ts"
            values = {"api_key": "sk-2", "base_url": "https://x/v1", "model": "m", "system_prompt": "hi"}
            with (
                mock.patch.object(dcore, "_local_config_source", return_value=(str(target), True)),
                mock.patch.object(dcore, "_stream", return_value=(0, "")),
            ):
                result = dcore.save_model_config("migpt", values)
            self.assertEqual(result["path"], str(target))
            self.assertIn("sk-2", target.read_text(encoding="utf-8"))

    def test_save_model_config_requires_container(self) -> None:
        values = {"api_key": "sk-2", "base_url": "https://x/v1", "model": "m", "system_prompt": "hi"}
        with mock.patch.object(dcore, "_local_config_source", return_value=("", False)):
            with self.assertRaises(dcore.DeployError) as ctx:
                dcore.save_model_config("migpt", values)
        self.assertIn("没有找到容器", str(ctx.exception))


class DeviceCoreTests(unittest.TestCase):
    def test_xp_render_preserves_other_keys(self) -> None:
        existing = "[realtime]\napp_id = old\nextra_key = keep\n\n[custom]\nfoo = bar\n"
        text = devcore.xp_render_config({"api_key": "new", "bot_name": "小瑞"}, existing)
        values = devcore.xp_values(text)
        self.assertEqual(values["api_key"], "new")
        self.assertEqual(values["bot_name"], "小瑞")
        self.assertIn("api_key = new", text)
        self.assertNotIn("app_id =", text)
        self.assertIn("extra_key = keep", text)
        self.assertIn("[custom]", text)

    def test_xp_status_parses(self) -> None:
        client = FakeSshClient(
            outputs={
                "echo installed=": (
                    b"installed=1\nconfig=1\nlog_size=12\nrunning=1\ndisk_kb=2048\nself_start=1\n"
                )
            }
        )
        client.files["/data/xiaoai-plus/config.ini"] = bytearray(b"[realtime]\napp_id = abc\n")
        client.files["/data/xiaoai-plus/keywords/keywords_doubao_repeat_end.txt"] = bytearray(
            "d òu b āo @豆包同学\n".encode("utf-8")
        )
        with mock.patch.object(devcore.dcore, "_ssh_connect", return_value=client):
            status = devcore.xp_status(devcore.dcore.SshConfig(host="example.com"))
        self.assertTrue(status["installed"])
        self.assertTrue(status["running"])
        self.assertTrue(status["self_start"])
        self.assertEqual(status["values"]["api_key"], "abc")
        self.assertEqual(status["wake_word"], "豆包同学")

    def test_xp_save_config_writes_file(self) -> None:
        client = FakeSshClient(sftp_raises=EOFError("EOF during negotiation"))
        client.files["/data/xiaoai-plus/config.ini"] = bytearray(b"[realtime]\napp_id = old\n")
        with mock.patch.object(devcore.dcore, "_ssh_connect", return_value=client):
            devcore.xp_save_config(
                devcore.dcore.SshConfig(host="example.com"), {"api_key": "new"}
            )
        text = bytes(client.files["/data/xiaoai-plus/config.ini"]).decode("utf-8")
        self.assertIn("api_key = new", text)
        self.assertNotIn("app_id =", text)

    def test_xp_control_commands(self) -> None:
        client = FakeSshClient(outputs={"RUNNING=1": b"RUNNING=1\n"})
        with mock.patch.object(devcore.dcore, "_ssh_connect", return_value=client):
            devcore.xp_control(devcore.dcore.SshConfig(host="example.com"), "restart")
        commands = "\n".join(client.commands)
        self.assertIn("kill", commands)
        self.assertIn("xiaoai_plus_speaker", commands)
        self.assertNotIn("nohup", devcore._xp_start_command())
        self.assertIn("RUNNING=1", devcore._xp_start_command())

    def test_hm_status_parses_state(self) -> None:
        client = FakeSshClient(outputs={"docker inspect": b"running\nlog line\n"})
        with mock.patch.object(devcore.dcore, "_ssh_connect", return_value=client):
            status = devcore.hm_status(devcore.dcore.SshConfig(host="192.168.1.10"))
        self.assertEqual(status["state"], "running")
        self.assertTrue(status["running"])

    def test_hm_deploy_uses_sudo(self) -> None:
        client = FakeSshClient(outputs={"docker run": b"container-id\n"})
        with mock.patch.object(devcore.dcore, "_ssh_connect", return_value=client):
            result = devcore.hm_deploy(
                devcore.dcore.SshConfig(host="192.168.1.10", user="deepin"),
                data_dir="/opt/home-mate/data",
                node_id="nas-core-1",
                use_sudo=True,
                sudo_password="pw",
            )
        self.assertEqual(result["container"], "home-mate-core")
        self.assertTrue(
            any(command.startswith("exec 2>&1; sudo -S") for command in client.commands)
        )

    def test_legacy_rsa_key_supports_ssh_rsa(self) -> None:
        try:
            import paramiko
        except ImportError:
            self.skipTest("paramiko 未安装")
        key = dcore._legacy_rsa_key(paramiko)
        self.assertIn("ssh-rsa", key.HASHES)

    def test_xp_config_template_has_api_key(self) -> None:
        keys = {key for _section, key, *_rest in devcore.XP_FIELDS}
        self.assertIn("api_key", keys)
        self.assertNotIn("app_id", keys)
        self.assertIn("api_key = your_api_key", devcore.XP_CONFIG_TEMPLATE)
        self.assertIn("api_key", devcore._xp_offline_script())

    def test_xp_render_config_writes_api_key(self) -> None:
        text = devcore.xp_render_config({"api_key": "ark-123"}, "[realtime]\napp_id = old\n")
        self.assertIn("api_key = ark-123", text)
        self.assertNotIn("app_id =", text)

    def test_patch_xp_binary_replaces_legacy_auth(self) -> None:
        data = (
            b"X-Api-App-ID\x00\x00\x00\x00"
            b"app_id\x00\x00realtime.app_id\x00"
            b"access_token\x00\x00\x00\x00realtime.access_token\x00"
            b"secret_key\x00\x00realtime.secret_key\x00"
        )
        patched = devcore._patch_xp_binary(data)
        self.assertIn(b"X-Api-Key\x00", patched)
        self.assertNotIn(b"X-Api-App-ID", patched)
        self.assertIn(b"apikey\x00", patched)
        self.assertIn(b"x_connect_id\x00", patched)
        self.assertIn(b"x_reserved\x00", patched)

    def test_patch_doubao_binary_speaks_chinese(self) -> None:
        data = b"X-Api-Key\x00Speak only English, every turn."
        patched = devcore._patch_xp_binary(data)
        self.assertNotIn(b"English", patched)
        self.assertIn(b"Speak only Chinese", patched)

    def test_xp_generate_keywords(self) -> None:
        try:
            import sherpa_onnx  # noqa: F401
        except ImportError:
            self.skipTest("sherpa-onnx 未安装")
        text = devcore.xp_generate_keywords("豆包同学")
        self.assertIn("@豆包同学", text)
        self.assertTrue(text.strip())

    def test_xp_archive_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            good = Path(temp) / "oh2p.tar.gz"
            with tarfile.open(good, "w:gz") as tar:
                payload = os.urandom(1_200_000)
                info = tarfile.TarInfo("oh2p/xiaoai_plus_speaker")
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
            self.assertTrue(devcore.xp_archive_ok(good))

            bad = Path(temp) / "bad.tar.gz"
            bad.write_bytes(b"not a tarball")
            self.assertFalse(devcore.xp_archive_ok(bad))

    def test_xp_offline_install_uploads_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "oh2p.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                payload = os.urandom(1_200_000)
                info = tarfile.TarInfo("oh2p/xiaoai_plus_speaker")
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
                example = b"[realtime]\napp_id = x\n"
                info2 = tarfile.TarInfo("oh2p/config.ini.example")
                info2.size = len(example)
                tar.addfile(info2, io.BytesIO(example))

            client = FakeSshClient(sftp_raises=EOFError("EOF during negotiation"))
            with (
                mock.patch.object(devcore.dcore, "_ssh_connect", return_value=client),
                mock.patch.object(devcore, "xp_patch_archive", side_effect=lambda path: path),
            ):
                result = devcore.xp_offline_install(
                    devcore.dcore.SshConfig(host="example.com"), str(archive)
                )

            self.assertTrue(result["installed"])
            self.assertIn("/tmp/xiaoai-plus-oh2p.tar.gz", client.files)
            commands = "\n".join(client.commands)
            self.assertIn("tar -xzf /tmp/xiaoai-plus-oh2p.tar.gz", commands)
            self.assertIn("/data/xiaoai-plus/xiaoai_plus_speaker", commands)

    def test_xp_offline_install_rejects_bad_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            bad = Path(temp) / "bad.tar.gz"
            bad.write_bytes(b"not a tarball")
            with self.assertRaises(dcore.DeployError):
                devcore.xp_offline_install(
                    dcore.SshConfig(host="example.com"), str(bad)
                )

    def test_xp_online_install_uses_patched_package(self) -> None:
        with mock.patch.object(
            devcore, "xp_offline_install", return_value={"installed": True}
        ) as offline:
            result = devcore.xp_install(dcore.SshConfig(host="example.com"))
        self.assertTrue(result["installed"])
        offline.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
