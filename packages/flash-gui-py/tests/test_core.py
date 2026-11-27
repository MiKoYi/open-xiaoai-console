"""刷机核心逻辑测试：用模拟 update.exe 跑完整流程，不需要真实音箱。

运行：
    python -m unittest discover -s tests -v
    或 python tests/test_core.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import flash_core as core  # noqa: E402
import flash_worker  # noqa: E402
import patch_core  # noqa: E402


def make_image(path: Path, magic: bytes = b"hsqs", size: int = 8 * 1024 * 1024) -> None:
    with path.open("wb") as handle:
        handle.write(magic)
        handle.truncate(size)


class FakeToolCase(unittest.TestCase):
    """每个测试用独立的临时目录，避免互相影响。"""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="xiaoi-flash-test-"))
        self.tool_dir = self.temp_dir / "tool"
        self.tool_dir.mkdir()
        fake_source = Path(__file__).resolve().parent / "fake_update.py"
        shutil.copyfile(fake_source, self.tool_dir / "update.py")
        self.update_exe = str(self.tool_dir / "update.py")

        self.lx06_image = self.temp_dir / "LX06_1.94.13_patched.squashfs"
        make_image(self.lx06_image)
        self.oh2p_image = self.temp_dir / "OH2P_1.58.6_patched.squashfs"
        make_image(self.oh2p_image)

        self._env_patch = mock.patch.dict(os.environ, {"FAKE_IDENTIFY_AFTER": "1"}, clear=False)
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        os.environ.pop("FAKE_MODE", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ---------------- 辅助 ----------------

    def reset_calls(self) -> None:
        for name in ("calls.log", "identify.count"):
            path = self.tool_dir / name
            if path.exists():
                path.unlink()

    def calls(self) -> list[str]:
        path = self.tool_dir / "calls.log"
        if not path.is_file():
            return []
        return path.read_text(encoding="utf-8").splitlines()

    def run_flow(self, action: str, image: Path | None = None, model: str = "LX06") -> list[str]:
        logs: list[str] = []

        def notify(kind: str, text: str, level: str) -> None:
            if kind == "log":
                logs.append(text)

        if action == "flash":
            core.start_flash_flow(self.update_exe, str(image), model, notify, timeout=10)
        elif action == "restore":
            core.start_restore_flow(self.update_exe, model, notify, timeout=10)
        elif action == "identify":
            core.test_device_flow(self.update_exe, notify, timeout=10)
        return logs

    # ---------------- 1. 固件校验 ----------------

    def test_firmware_empty_and_missing(self) -> None:
        self.assertFalse(core.test_firmware("", "LX06").ok)
        self.assertFalse(core.test_firmware(str(self.temp_dir / "nope.squashfs"), "LX06").ok)

    def test_firmware_bad_magic(self) -> None:
        bad = self.temp_dir / "LX06_bad.squashfs"
        make_image(bad, magic=b"XXXX")
        self.assertFalse(core.test_firmware(str(bad), "LX06").ok)

    def test_firmware_too_large(self) -> None:
        big = self.temp_dir / "LX06_1.94.13_patched_big.squashfs"
        make_image(big, size=41 * 1024 * 1024)
        self.assertFalse(core.test_firmware(str(big), "LX06").ok)

    def test_firmware_valid(self) -> None:
        check = core.test_firmware(str(self.lx06_image), "LX06")
        self.assertTrue(check.ok, check.error)
        self.assertEqual(check.size, 8 * 1024 * 1024)

    def test_firmware_wrong_model(self) -> None:
        self.assertFalse(core.test_firmware(str(self.lx06_image), "OH2P").ok)
        self.assertFalse(core.test_firmware(str(self.oh2p_image), "LX06").ok)

    def test_firmware_version_mismatch_warns(self) -> None:
        old = self.temp_dir / "LX06_1.88.206_patched.squashfs"
        make_image(old)
        check = core.test_firmware(str(old), "LX06")
        self.assertTrue(check.ok)
        self.assertTrue(check.warnings)

    def test_firmware_unknown_name_rejected(self) -> None:
        plain = self.temp_dir / "mystery.squashfs"
        make_image(plain)
        self.assertFalse(core.test_firmware(str(plain), "LX06").ok)

    def test_firmware_candidates_auto_prefers_mirrors(self) -> None:
        info = core.get_model_info("LX06")
        candidates = core.firmware_candidates(info, "auto")
        self.assertGreaterEqual(len(candidates), 2)
        self.assertTrue(candidates[0][1].startswith("https://ghproxy.net/"))
        self.assertEqual(candidates[-1], ("GitHub 直连", info.url))

    def test_firmware_candidates_direct(self) -> None:
        info = core.get_model_info("OH2P")
        self.assertEqual(core.firmware_candidates(info, "github"), [("GitHub 直连", info.url)])

    def test_firmware_candidates_custom_requires_prefix(self) -> None:
        info = core.get_model_info("LX06")
        with self.assertRaises(core.FlashError):
            core.firmware_candidates(info, "custom", "")
        candidates = core.firmware_candidates(info, "custom", "https://example.com/")
        self.assertEqual(candidates[0][1], "https://example.com/" + info.url)

    def test_firmware_candidates_official(self) -> None:
        info = core.get_model_info("LX06")
        candidates = core.firmware_candidates(info, "auto", kind="official")
        self.assertIn(info.official_filename, candidates[0][1])
        self.assertEqual(candidates[-1], ("GitHub 直连", info.official_url))

    # ---------------- 2. 设备识别 ----------------

    def test_wait_for_device_retries(self) -> None:
        self.reset_calls()
        os.environ["FAKE_IDENTIFY_AFTER"] = "3"
        device = core.wait_for_device(self.update_exe, timeout=10)
        self.assertEqual(device.version, "0-7-0-16-0-0-0-0")
        self.assertEqual(len(self.calls()), 3)

    def test_wait_for_device_timeout(self) -> None:
        self.reset_calls()
        os.environ["FAKE_IDENTIFY_AFTER"] = "9999"
        with self.assertRaises(core.FlashError) as ctx:
            core.wait_for_device(self.update_exe, timeout=2)
        self.assertIn("等待超时", str(ctx.exception))

    def test_wait_for_device_reports_usb_error_once(self) -> None:
        self.reset_calls()
        os.environ["FAKE_MODE"] = "usb-error"
        logs: list[str] = []

        def notify(kind: str, text: str, level: str) -> None:
            if kind == "log":
                logs.append(text)

        with self.assertRaises(core.FlashError):
            core.wait_for_device(self.update_exe, timeout=2, notify=notify)

        hints = [text for text in logs if "USB 通信失败" in text]
        self.assertEqual(len(hints), 1)
        self.assertIn("USB 2.0", hints[0])

    def test_usb_error_hint_ignores_normal_misses(self) -> None:
        self.assertEqual(core._usb_error_hint("AmlUsbIdentifyHost\ncan not find device"), "")
        self.assertIn("USB 通信失败", core._usb_error_hint("libusb0-dll:err [control_msg] failed"))

    # ---------------- 3. 刷机流程 ----------------

    def test_flash_success(self) -> None:
        self.reset_calls()
        logs = self.run_flow("flash", self.lx06_image)
        joined = "\n".join(self.calls())
        self.assertIn("bulkcmd setenv bootdelay 15", joined)
        self.assertIn("bulkcmd setenv boot_part boot0", joined)
        self.assertIn("bulkcmd saveenv", joined)
        self.assertIn("partition system0", joined)
        self.assertTrue(any("刷机完成" in line for line in logs))
        self.assertTrue(any("已识别到设备" in line for line in logs))

    def test_partition_failure_message(self) -> None:
        self.reset_calls()
        os.environ["FAKE_MODE"] = "fail-partition"
        with self.assertRaises(core.FlashError) as ctx:
            self.run_flow("flash", self.lx06_image)
        message = str(ctx.exception)
        self.assertIn("Usb status not success", message)
        self.assertIn("USB", message)

    def test_bulkcmd_retries(self) -> None:
        self.reset_calls()
        os.environ["FAKE_MODE"] = "fail-bulk"
        with self.assertRaises(core.FlashError) as ctx:
            self.run_flow("flash", self.lx06_image)
        self.assertIn("设置启动参数失败", str(ctx.exception))
        bulk_calls = [line for line in self.calls() if line.startswith("bulkcmd")]
        self.assertEqual(len(bulk_calls), 6)

    def test_restore(self) -> None:
        self.reset_calls()
        self.run_flow("restore")
        joined = "\n".join(self.calls())
        self.assertIn("bulkcmd setenv boot_part boot1", joined)
        self.assertIn("bulkcmd saveenv", joined)

    def test_identify_only(self) -> None:
        self.reset_calls()
        logs = self.run_flow("identify")
        self.assertTrue(any("识别成功" in line for line in logs))

    # ---------------- 4. 机型保护 ----------------

    def test_wrong_model_blocks_before_any_command(self) -> None:
        self.reset_calls()
        with self.assertRaises(core.FlashError) as ctx:
            self.run_flow("flash", self.lx06_image, model="OH2P")
        self.assertIn("不是 Xiaomi 智能音箱 Pro", str(ctx.exception))
        self.assertEqual(self.calls(), [])

    def test_oh2p_flash_success(self) -> None:
        self.reset_calls()
        logs = self.run_flow("flash", self.oh2p_image, model="OH2P")
        joined = "\n".join(self.calls())
        self.assertIn("bulkcmd setenv boot_part boot0", joined)
        self.assertIn("partition system0", joined)
        self.assertTrue(any("OH2P" in line for line in logs))

    def test_oh2p_restore(self) -> None:
        self.reset_calls()
        self.run_flow("restore", model="OH2P")
        joined = "\n".join(self.calls())
        self.assertIn("bulkcmd setenv boot_part boot1", joined)

    # ---------------- 5. 异常 ----------------

    def test_missing_update_exe(self) -> None:
        with self.assertRaises(core.FlashError) as ctx:
            core.run_update(str(self.temp_dir / "missing.exe"), ["identify"])
        self.assertIn("找不到 update.exe", str(ctx.exception))


class WorkerTests(unittest.TestCase):
    """flash_worker 是提权子进程，通过 JSONL 事件文件和控制台通信，这里只测事件内容。"""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="xiaoi-worker-test-"))
        fake_source = Path(__file__).resolve().parent / "fake_update.py"
        self.update_exe = str(self.temp_dir / "update.py")
        shutil.copyfile(fake_source, self.update_exe)
        self.events = self.temp_dir / "events.jsonl"
        self._env_patch = mock.patch.dict(os.environ, {"FAKE_IDENTIFY_AFTER": "1"}, clear=False)
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        os.environ.pop("FAKE_MODE", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def run_worker(self, *args: str) -> tuple[int, list[dict]]:
        code = flash_worker.main(["--exe", self.update_exe, "--events", str(self.events), *args])
        lines = self.events.read_text(encoding="utf-8").splitlines()
        return code, [json.loads(line) for line in lines]

    def test_identify_writes_result_and_logs(self) -> None:
        code, events = self.run_worker("--action", "identify", "--timeout", "5")
        self.assertEqual(code, 0)
        self.assertEqual(events[-1]["type"], "result")
        self.assertEqual(events[-1]["version"], "0-7-0-16-0-0-0-0")
        self.assertTrue(any(event["type"] == "log" for event in events))
        self.assertTrue(any(event["type"] == "status" for event in events))

    def test_flash_error_is_written(self) -> None:
        missing = self.temp_dir / "nope.squashfs"
        code, events = self.run_worker("--action", "flash", "--image", str(missing))
        self.assertEqual(code, 1)
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("固件文件不存在", events[-1]["text"])

    def test_run_flash_action_inline(self) -> None:
        version = core.run_flash_action(
            "identify", self.update_exe, notify=None, timeout=5, elevate=False
        )
        self.assertEqual(version, "0-7-0-16-0-0-0-0")

    def test_run_flash_action_rejects_unknown(self) -> None:
        with self.assertRaises(core.FlashError):
            core.run_flash_action("nope", self.update_exe, elevate=False)

    def test_read_events_waits_for_complete_line(self) -> None:
        path = self.temp_dir / "events.jsonl"
        path.write_text('{"type": "log", "text": "半行', encoding="utf-8")
        events, seen = core._read_events(path, 0)
        self.assertEqual(events, [])
        with path.open("a", encoding="utf-8") as handle:
            handle.write('"}\n')
        events, seen = core._read_events(path, seen)
        self.assertEqual(events[0]["text"], "半行")
        self.assertEqual(core._read_events(path, seen)[0], [])

    def test_consume_events_forwards_to_notify(self) -> None:
        logs: list[tuple[str, str, str]] = []

        def notify(kind: str, text: str, level: str = "") -> None:
            logs.append((kind, text, level))

        version, error = core._consume_events(
            [
                {"type": "log", "text": "hi", "level": "warn"},
                {"type": "status", "text": "忙"},
                {"type": "result", "version": "1"},
            ],
            notify,
            "",
            "",
        )
        self.assertEqual((version, error), ("1", ""))
        self.assertEqual(logs, [("log", "hi", "warn"), ("status", "忙", "")])


class PatchNamingTests(unittest.TestCase):
    def test_output_version_uses_input_version(self) -> None:
        source = Path("/tmp/OH2P_1.60.2.squashfs")
        self.assertEqual(patch_core._output_version(source, "OH2P"), "1.60.2")
        self.assertTrue(
            str(patch_core._output_path("OH2P", "1.60.2")).replace("\\", "/").endswith(
                "firmware/OH2P_1.60.2_patched.squashfs"
            )
        )

    def test_output_version_falls_back_to_registry(self) -> None:
        self.assertEqual(patch_core._output_version(Path("/tmp/official.squashfs"), "OH2P"), "1.60.2")

    def test_remote_arch_detection(self) -> None:
        with mock.patch.object(patch_core.dcore, "_ssh_run", return_value=(0, "aarch64\n")):
            self.assertEqual(patch_core._remote_arch(object()), "aarch64")
        with mock.patch.object(patch_core.dcore, "_ssh_run", return_value=(1, "")):
            self.assertEqual(patch_core._remote_arch(object()), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
