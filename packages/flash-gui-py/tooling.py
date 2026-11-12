"""内置刷机工具：支持 tools/ 下的文件夹或 zip 压缩包。"""

from __future__ import annotations

import zipfile
from pathlib import Path


def find_update_exe(root: Path | str) -> str:
    """在 tools/ 下递归查找 update.exe。"""
    tools = Path(root) / "tools"
    if not tools.is_dir():
        return ""

    preferred = [
        tools / "Amlogic_Flash_Tool_v6.0.0" / "bin" / "update.exe",
        tools / "update.exe",
    ]
    for path in preferred:
        if path.is_file():
            return str(path)

    try:
        for path in tools.rglob("update.exe"):
            if path.is_file():
                return str(path)
    except OSError:
        pass
    return ""


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    destination = destination.resolve()
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        if not name:
            continue
        candidate = Path(name)
        if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
            continue
        target = (destination / name).resolve()
        if target != destination and destination not in target.parents:
            continue
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, target.open("wb") as handle:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)


def ensure_tool(root: Path | str) -> tuple[str, str, str, str]:
    """确保内置刷机工具可用。

    返回 (update.exe 路径, 来源, zip 文件名, 错误信息)
    来源：folder（已经是解压好的目录）/ zip（刚解压）/ none（没有工具）
    """
    root_path = Path(root)
    tools = root_path / "tools"

    archives = sorted(tools.glob("*.zip")) if tools.is_dir() else []

    exe = find_update_exe(root_path)
    if exe:
        if archives:
            return exe, "zip", archives[0].name, ""
        return exe, "folder", "", ""

    if not tools.is_dir():
        return "", "none", "", ""
    if not archives:
        return "", "none", "", ""

    archive_path = archives[0]
    try:
        with zipfile.ZipFile(archive_path) as archive:
            _safe_extract(archive, tools)
    except (zipfile.BadZipFile, OSError) as exc:
        return "", "zip", archive_path.name, f"解压失败：{exc}"

    exe = find_update_exe(root_path)
    if exe:
        return exe, "zip", archive_path.name, ""
    return "", "zip", archive_path.name, "压缩包里没有找到 update.exe"
