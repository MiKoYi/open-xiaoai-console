"""刷机核心逻辑测试：用模拟 update.exe 跑完整流程，不需要真实音箱。

运行：
    python -m unittest discover -s tests -v
    或 python tests/test_core.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import flash_core as core  # noqa: E402


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
