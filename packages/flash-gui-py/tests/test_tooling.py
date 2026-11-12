"""内置刷机工具（zip / 文件夹）测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tooling  # noqa: E402


class ToolingTests(unittest.TestCase):
    def make_root(self, temp: str) -> Path:
        root = Path(temp)
        (root / "tools").mkdir()
        return root

    def test_zip_is_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_root(temp)
            with zipfile.ZipFile(root / "tools" / "Amlogic_Flash_Tool_v6.0.0.zip", "w") as archive:
                archive.writestr("Amlogic_Flash_Tool_v6.0.0/bin/update.exe", b"MZ")

            exe, source, zip_name, error = tooling.ensure_tool(root)
            self.assertEqual(source, "zip")
            self.assertEqual(zip_name, "Amlogic_Flash_Tool_v6.0.0.zip")
            self.assertEqual(error, "")
            self.assertTrue(Path(exe).is_file())

            exe2, source2, _, _ = tooling.ensure_tool(root)
            self.assertEqual(source2, "zip")
            self.assertEqual(exe2, exe)

            (root / "tools" / "Amlogic_Flash_Tool_v6.0.0.zip").unlink()
            _, source3, _, _ = tooling.ensure_tool(root)
            self.assertEqual(source3, "folder")

    def test_zip_without_update_exe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_root(temp)
            with zipfile.ZipFile(root / "tools" / "tool.zip", "w") as archive:
                archive.writestr("readme.txt", "nothing here")

            exe, source, _, error = tooling.ensure_tool(root)
            self.assertEqual(exe, "")
            self.assertEqual(source, "zip")
            self.assertIn("update.exe", error)

    def test_no_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            exe, source, _, _ = tooling.ensure_tool(root)
            self.assertEqual(exe, "")
            self.assertEqual(source, "none")

    def test_zip_slip_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_root(temp)
            with zipfile.ZipFile(root / "tools" / "evil.zip", "w") as archive:
                archive.writestr("../evil.txt", "x")
                archive.writestr("bin/update.exe", b"MZ")

            tooling.ensure_tool(root)
            self.assertFalse((root / "evil.txt").exists())
            self.assertFalse((Path(temp).parent / "evil.txt").exists())

    def test_folder_is_used_directly(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_root(temp)
            exe_path = root / "tools" / "Amlogic_Flash_Tool_v6.0.0" / "bin" / "update.exe"
            exe_path.parent.mkdir(parents=True)
            exe_path.write_bytes(b"MZ")

            exe, source, _, _ = tooling.ensure_tool(root)
            self.assertEqual(source, "folder")
            self.assertEqual(exe, str(exe_path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
