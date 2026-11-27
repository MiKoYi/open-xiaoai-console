"""小爱音箱刷机核心逻辑（支持 LX06 / OH2P）。

不依赖图形界面，也不依赖任何第三方库，可单独 import 或测试。
仅支持 Windows（Amlogic 刷机工具是 Windows 程序）。
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from urllib.request import Request, urlopen

# ---------------- 常量 ----------------

IMAGE_MAX_SIZE = 0x02800000  # 40MB，system 分区上限（见 packages/client-patch/src/squashfs.sh）
# bootrom 只在上电后几秒内可见，轮询要尽量密，才抓得住这个窗口
IDENTIFY_INTERVAL = 0.1
TUTORIAL_URL = "https://github.com/idootop/open-xiaoai/blob/main/docs/flash.md"
TOOL_URL = "https://www.mediafire.com/file/1m9x18q1klcj5ds/Amlogic_Flash_Tool_v6.0.0.zip/file"
RELEASE_BASE = "https://github.com/idootop/open-xiaoai/releases/download"

# 固件下载加速源；auto 会按这个顺序尝试，最后再回退直连 GitHub
FIRMWARE_MIRRORS = (
    ("ghproxy.net", "https://ghproxy.net/"),
    ("gh-proxy.com", "https://gh-proxy.com/"),
    ("ghfast.top", "https://ghfast.top/"),
)


class FlashError(Exception):
    """可以直接展示给用户的错误。"""


@dataclass(frozen=True)
class ModelInfo:
    model: str
    name: str
    version: str
    filename: str
    url: str
    sha256: str
    connection: str

    @property
    def release_page(self) -> str:
        return f"https://github.com/idootop/open-xiaoai/releases/tag/{self.model}_{self.version}"

    @property
    def official_filename(self) -> str:
        return f"{self.model}_{self.version}.squashfs"

    @property
    def official_url(self) -> str:
        return f"{RELEASE_BASE}/{self.model}_{self.version}/{self.official_filename}"


MODELS = {
    "LX06": ModelInfo(
        model="LX06",
        name="小爱音箱 Pro（LX06）",
        version="1.94.13",
        filename="LX06_1.94.13_patched.squashfs",
        url=f"{RELEASE_BASE}/LX06_1.94.13/LX06_1.94.13_patched.squashfs",
        sha256="3fbcfcd8a2130f5421d0bc3a3b22cb5a3de59b5d3678a3a14b23541aa9474225",
        connection="需要拆机，把 Micro USB 数据线接到主板调试口",
    ),
    "OH2P": ModelInfo(
        model="OH2P",
        name="Xiaomi 智能音箱 Pro（OH2P）",
        version="1.58.6",
        filename="OH2P_1.58.6_patched.squashfs",
        url=f"{RELEASE_BASE}/OH2P_1.58.6/OH2P_1.58.6_patched.squashfs",
        sha256="1ff342defa0dabe552d69ebeb4c20e0321d2782437f9eb30bc0f6e10de328aa4",
        connection="无需拆机，直接用 Type-C 数据线连接音箱底部接口",
    ),
}

MODEL_ORDER = ("LX06", "OH2P")


def get_model_info(model: str) -> ModelInfo:
    try:
        return MODELS[model]
    except KeyError as exc:
        raise FlashError(f"不支持的机型：{model}（仅支持 LX06 / OH2P）") from exc


def firmware_candidates(
    info: ModelInfo, mirror: str = "auto", custom_prefix: str = "", kind: str = "patched"
) -> list[tuple[str, str]]:
    """返回固件下载候选 [(显示名, URL), ...]。

    kind = patched（补丁固件）或 official（官方原版固件）。
    mirror 与部署页的下载源设置保持一致：auto / github / ghfast / ghproxy /
    gitmirror / custom。auto 优先走国内加速源，最后回退 GitHub 直连。
    """
    source_url = info.official_url if kind == "official" else info.url
    direct = ("GitHub 直连", source_url)

    if mirror == "custom":
        prefix = (custom_prefix or "").strip()
        if not prefix:
            raise FlashError("请先填写自定义加速前缀，例如 https://ghproxy.net/")
        if not prefix.endswith("/"):
            prefix += "/"
        return [("自定义源", prefix + source_url), direct]

    if mirror in ("github", "direct"):
        return [direct]

    presets = {
        "ghfast": ("ghfast.top", "https://ghfast.top/"),
        "ghproxy": ("ghproxy.net", "https://ghproxy.net/"),
        "gitmirror": ("hub.gitmirror.com", "https://hub.gitmirror.com/"),
    }
    if mirror in presets:
        label, prefix = presets[mirror]
        return [(label, prefix + source_url), direct]

    return [(label, prefix + source_url) for label, prefix in FIRMWARE_MIRRORS] + [direct]


# ---------------- 通知 ----------------
# notify(kind, text, level)：kind = "log" | "status"；level = info | ok | warn | error

Notify = Optional[Callable[[str, str, str], None]]


def _log(notify: Notify, text: str, level: str = "info") -> None:
    if notify:
        notify("log", text, level)


def _status(notify: Notify, text: str) -> None:
    if notify:
        notify("status", text, "")


# ---------------- 执行 update.exe ----------------


@dataclass
class ProcResult:
    exit_code: int
    output: str
    error: str = ""
    timed_out: bool = False

    @property
    def text(self) -> str:
        return (self.output + "\n" + self.error).strip()


def _build_command(update_exe: str, args: list[str]) -> list[str]:
    """组装命令。

    真实场景传的是 update.exe；额外支持 .py / .cmd / .bat，
    方便在没有音箱时用模拟脚本跑通整个流程（见 tests/）。
    """
    suffix = Path(update_exe).suffix.lower()
    if suffix == ".py":
        return [sys.executable, update_exe, *args]
    if suffix in (".cmd", ".bat"):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", update_exe, *args]
    return [update_exe, *args]


def run_update(update_exe: str, args: list[str], timeout: float = 30) -> ProcResult:
    """同步执行一次刷机工具命令。"""
    exe_path = Path(update_exe)
    if not exe_path.is_file():
        raise FlashError(f"找不到 update.exe：{update_exe}")

    command = _build_command(str(exe_path), args)
    kwargs: dict = {
        "cwd": str(exe_path.resolve().parent),
        "capture_output": True,
        "text": True,
        "encoding": "gbk",
        "errors": "replace",
        "timeout": timeout,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        completed = subprocess.run(command, **kwargs)
        return ProcResult(completed.returncode, completed.stdout or "", completed.stderr or "")
    except subprocess.TimeoutExpired as exc:
        output = _to_text(exc.stdout)
        error = _to_text(exc.stderr)
        return ProcResult(-1, output, error, timed_out=True)
    except OSError as exc:
        raise FlashError(f"无法运行 update.exe：{exc}") from exc


def _to_text(value) -> str:
    if not value:
        return ""
    if isinstance(value, bytes):
        return value.decode("gbk", "replace")
    return value


# ---------------- 管理员权限 ----------------
# update.exe 访问 USB 设备需要管理员权限，但控制台界面（WebView2）不支持以管理员运行：
# 界面保持普通权限，刷机时用 ShellExecuteEx(runas) 单独拉起 flash_worker 子进程，
# 子进程把日志和结果写成 JSONL 事件文件，父进程边读边显示。

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_HIDE = 0
ERROR_CANCELLED = 1223
WAIT_OBJECT_0 = 0
FLASH_WORKER = Path(__file__).with_name("flash_worker.py")


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("fMask", ctypes.c_uint32),
        ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p),
        ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_uint32),
        ("hIcon", ctypes.c_void_p),
        ("hProcess", ctypes.c_void_p),
    ]


def is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001 - 判断失败就按普通权限处理
        return False


def _start_elevated_worker(events: Path, action: str, update_exe: str, image_path: str, model: str, timeout: int) -> int:
    if not FLASH_WORKER.is_file():
        raise FlashError("缺少 flash_worker.py，无法以管理员权限运行刷机工具。")
    parameters = subprocess.list2cmdline(
        [
            str(FLASH_WORKER),
            "--action", action,
            "--exe", str(update_exe),
            "--events", str(events),
            "--image", str(image_path or ""),
            "--model", str(model),
            "--timeout", str(int(timeout)),
        ]
    )
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = ctypes.c_bool
    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = sys.executable
    info.lpParameters = parameters
    info.lpDirectory = str(Path(__file__).resolve().parent)
    info.nShow = SW_HIDE
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        code = ctypes.get_last_error()
        if code == ERROR_CANCELLED:
            raise FlashError("刷机需要管理员权限：请在弹出的 UAC 窗口中点「是」，然后重试。")
        raise FlashError(f"无法以管理员权限启动刷机进程（错误代码 {code}），请重试。")
    return int(info.hProcess or 0)


def _read_events(path: Path, seen: int) -> tuple[list[dict], int]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], seen
    # 只处理完整的行，避免把正在写的一行当成两条事件
    text = text[: text.rfind("\n") + 1]
    lines = text.splitlines()
    events: list[dict] = []
    for line in lines[seen:]:
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events, len(lines)


def _consume_events(events: list[dict], notify: Notify, version: str, error: str) -> tuple[str, str]:
    for event in events:
        kind = event.get("type")
        if kind == "log":
            _log(notify, str(event.get("text", "")), str(event.get("level") or "info"))
        elif kind == "status":
            _status(notify, str(event.get("text", "")))
        elif kind == "result":
            version = str(event.get("version", ""))
        elif kind == "error":
            error = str(event.get("text", ""))
    return version, error


def _pump_events(handle: int, events: Path, notify: Notify, wait_seconds: float) -> str:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_bool
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    seen = 0
    version = ""
    error = ""
    deadline = time.monotonic() + wait_seconds
    try:
        while True:
            found, seen = _read_events(events, seen)
            version, error = _consume_events(found, notify, version, error)
            if kernel32.WaitForSingleObject(handle, 100) == WAIT_OBJECT_0:
                break
            if time.monotonic() > deadline:
                try:
                    kernel32.TerminateProcess(handle, 1)
                except OSError:
                    pass
                raise FlashError("刷机进程长时间没有结束，已终止。请重试。")
        time.sleep(0.1)  # 等子进程把最后几行日志写完
        found, seen = _read_events(events, seen)
        version, error = _consume_events(found, notify, version, error)
        exit_code = ctypes.c_uint32()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
    finally:
        kernel32.CloseHandle(handle)

    if error:
        raise FlashError(error)
    if exit_code.value != 0:
        raise FlashError(f"刷机进程异常退出（代码 {exit_code.value}），请重试。")
    return version


def _run_elevated(action: str, update_exe: str, image_path: str, model: str, notify: Notify, timeout: int) -> str:
    with tempfile.TemporaryDirectory(prefix="open-xiaoai-flash-") as temp:
        events = Path(temp) / "events.jsonl"
        events.touch()
        handle = _start_elevated_worker(events, action, update_exe, image_path, model, timeout)
        return _pump_events(handle, events, notify, timeout + 600)


def run_flash_action(
    action: str,
    update_exe: str,
    image_path: str = "",
    model: str = "LX06",
    notify: Notify = None,
    timeout: int = 180,
    elevate: Optional[bool] = None,
) -> str:
    """执行刷机动作（flash / restore / identify），返回识别到的 bootrom 版本。

    elevate=None 时按当前权限自动判断：Windows 上不是管理员就拉起提权子进程，
    保证 update.exe 以管理员权限访问 USB 设备；已经是管理员则直接在本进程执行。
    """
    if elevate is None:
        elevate = os.name == "nt" and not is_admin()
    if elevate and os.name == "nt":
        return _run_elevated(action, update_exe, image_path, model, notify, timeout)

    if action == "flash":
        start_flash_flow(update_exe, image_path, model, notify=notify, timeout=timeout)
        return ""
    if action == "restore":
        start_restore_flow(update_exe, model, notify=notify, timeout=timeout)
        return ""
    if action == "identify":
        return test_device_flow(update_exe, notify=notify, timeout=timeout).version
    raise FlashError(f"未知的刷机操作：{action}")


# ---------------- 等待设备 ----------------


@dataclass
class Device:
    version: str
    attempts: int


# update.exe 的 USB 报错 → 直接给可操作的排查建议（避免只显示“仍在等待设备”）
_USB_ERROR_HINTS = (
    (
        ("libusb0", "control_msg", "ioctl_identify_host"),
        "USB 通信失败（update.exe 报 libusb0 / control_msg 错误）：音箱在识别时掉线，或 USB 驱动 / 接口不稳定。请依次排查：\n"
        "  1. 在控制台开始识别后再重新给音箱上电，识别窗口只有几秒钟；\n"
        "  2. 关闭其他刷机工具，确保只有一个程序在访问设备；\n"
        "  3. 换主板后置的 USB 2.0 接口、换一根数据线再试；\n"
        "  4. 设备管理器里「WorldCup Device」带黄色感叹号时，运行刷机工具里的驱动安装程序重装驱动。",
    ),
)


def _usb_error_hint(output: str) -> str:
    text = (output or "").lower()
    for markers, hint in _USB_ERROR_HINTS:
        if any(marker in text for marker in markers):
            return hint
    return ""


def wait_for_device(
    update_exe: str,
    timeout: int = 180,
    notify: Notify = None,
    cancel: Optional[Callable[[], bool]] = None,
) -> Device:
    """反复执行 identify，直到在 bootrom 里发现音箱。

    设备上电后只可见几秒钟，所以这里持续轮询，用户只需要重新上电。
    """
    deadline = time.monotonic() + timeout
    last_hint = time.monotonic()
    attempt = 0
    shown_hints: set[str] = set()

    while time.monotonic() < deadline:
        if cancel and cancel():
            raise FlashError("操作已取消。")
        attempt += 1
        result = run_update(update_exe, ["identify"], timeout=10)
        match = re.search(r"This firmware version is\s+(\S+)", result.output)
        if match:
            return Device(match.group(1), attempt)

        hint = _usb_error_hint(result.output)
        if hint and hint not in shown_hints:
            shown_hints.add(hint)
            _log(notify, hint, "warn")

        now = time.monotonic()
        if now - last_hint >= 15:
            _log(notify, "仍在等待设备… 请确认：数据线支持传输、USB 驱动已安装、并且刚刚重新插上过电源。", "warn")
            last_hint = now

        time.sleep(IDENTIFY_INTERVAL)

    raise FlashError(
        f"等待超时（{timeout} 秒）：没有识别到音箱。请检查数据线是否支持数据传输、"
        "USB 驱动是否装好，然后重新拔插电源再试。"
    )


# ---------------- 设置启动参数 ----------------


def bulk_cmd(update_exe: str, command: str, attempts: int = 6, notify: Notify = None) -> ProcResult:
    """update.exe 的 bulkcmd 第一次经常返回 strncmp != 0，需要重试。"""
    for index in range(1, attempts + 1):
        result = run_update(update_exe, ["bulkcmd", command], timeout=15)
        ok = "Read status return success" in result.output and "ERR:" not in result.output
        if ok:
            return result
        if index < attempts:
            _log(notify, f"设置参数重试 {index}/{attempts}：{command}", "warn")
            time.sleep(0.5)

    raise FlashError(f"设置启动参数失败：{command}。请重新拔插电源后再试一次（部分设备需要多试几次）。")


# ---------------- 写入 system0 ----------------


def install_system(update_exe: str, image_path: str, notify: Notify = None) -> ProcResult:
    _log(notify, "开始写入 system0 分区（约 10-30 秒，请不要拔线、不要断电）…")
    _status(notify, "正在写入 system0 分区…")

    result = run_update(update_exe, ["partition", "system0", str(image_path)], timeout=300)

    match = re.search(r"file size is\s+(\S+)", result.output)
    if match:
        _log(notify, f"固件写入大小：{match.group(1)}")
    match = re.search(r"Transfer size\s+(\S+)", result.output)
    if match:
        _log(notify, f"已传输：{match.group(1)}")

    if "mwrite success" in result.output:
        return result

    if result.timed_out:
        raise FlashError("写入超时：设备可能已断开。请换一个 USB 接口（优先主板后置口）后重试。")
    if "Usb status not success" in result.output:
        raise FlashError("写入失败（Usb status not success）：通常是 USB 口或数据线的问题。请换一个 USB 接口 / 换一根数据线后重试。")
    if ("can not find device" in result.text or "no device" in result.text) and "Downloading" not in result.output:
        raise FlashError("写入前设备已断开：请确认数据线连接稳定，并在提示后重新给音箱上电。")

    raise FlashError(f"写入失败，update.exe 返回：\n{result.text}")


# ---------------- 固件校验 ----------------


@dataclass
class FirmwareCheck:
    ok: bool = False
    error: str = ""
    size: int = 0
    warnings: list[str] = field(default_factory=list)


def test_firmware(path: str, model: str = "LX06") -> FirmwareCheck:
    info = get_model_info(model)
    result = FirmwareCheck()

    if not path or not str(path).strip():
        result.error = "请先选择补丁固件文件（.squashfs）。"
        return result

    file_path = Path(path)
    if not file_path.is_file():
        result.error = f"固件文件不存在：{file_path}"
        return result

    size = file_path.stat().st_size
    result.size = size

    if size < 1024 * 1024:
        result.error = "这个文件太小了，不像是完整固件。请重新下载。"
        return result
    if size >= IMAGE_MAX_SIZE:
        result.error = (
            f"固件大小 {size / 1024 / 1024:.1f} MB 超过了 40 MB 分区上限，刷不进去。"
            f"请确认下载的是 {info.filename}。"
        )
        return result

    try:
        with file_path.open("rb") as handle:
            magic = handle.read(4)
    except OSError as exc:
        result.error = f"读取固件失败：{exc}"
        return result

    if magic != b"hsqs":
        result.error = "这不是有效的 squashfs 固件文件（文件头不对）。请确认下载的是 .squashfs 文件，而不是网页或压缩包。"
        return result

    name_lower = file_path.name.lower()
    if info.model.lower() not in name_lower:
        result.error = f"这不是 {info.name} 的固件（文件名：{file_path.name}）。请重新选择或下载 {info.filename}。"
        return result

    version_match = re.search(r"(\d+\.\d+\.\d+)", file_path.name)
    if version_match and version_match.group(1) != info.version:
        result.warnings.append(
            f"固件版本 {version_match.group(1)} 与工具内置的 {info.version} 不一致，"
            f"请确认设备当前固件也是 {version_match.group(1)}，跨版本刷机有风险。"
        )

    if "_patched" not in name_lower:
        result.warnings.append("文件名里没有 _patched，这可能是原厂固件——刷入后不会开启 SSH。")

    result.ok = True
    return result


# ---------------- 下载官方固件 ----------------


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_firmware(
    dest_dir: str | Path,
    model: str = "LX06",
    notify: Notify = None,
    mirror: str = "auto",
    custom_prefix: str = "",
    kind: str = "patched",
) -> Path:
    info = get_model_info(model)
    official = kind == "official"
    filename = info.official_filename if official else info.filename
    source_url = info.official_url if official else info.url
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / filename

    if target.is_file():
        if official:
            check = test_firmware(str(target), model)
            if check.ok:
                _log(notify, "已有官方固件校验通过，直接使用。", "ok")
                return target
        elif sha256_file(target) == info.sha256:
            _log(notify, "已有固件校验通过，直接使用。", "ok")
            return target
        _log(notify, "已有文件校验不通过，重新下载…", "warn")
        target.unlink()

    candidates = firmware_candidates(info, mirror, custom_prefix, kind)
    errors: list[str] = []
    part = target.with_suffix(target.suffix + ".part")
    part.unlink(missing_ok=True)

    for label, url in candidates:
        _log(notify, f"正在从 {label} 下载 {filename}…")
        _status(notify, f"正在下载固件…（{label}）")
        downloaded = 0
        last_percent = -1
        try:
            request = Request(url, headers={"User-Agent": "open-xiaoai-flash-gui"})
            with urlopen(request, timeout=45) as response:
                total = int(response.headers.get("Content-Length") or 0)
                with part.open("wb") as handle:
                    while True:
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            percent = downloaded * 100 // total
                            if percent >= last_percent + 5:
                                last_percent = percent
                                _log(notify, f"下载中… {percent}%")
                                _status(notify, f"正在下载固件… {percent}%")
            if part.stat().st_size < 20 * 1024 * 1024:
                raise FlashError("下载到的文件不完整")
            if total and downloaded < total:
                raise FlashError("下载中断，请重试")
            part.replace(target)
            break
        except Exception as exc:  # noqa: BLE001 - 网络错误类型很多
            part.unlink(missing_ok=True)
            errors.append(f"{label}：{exc}")
            _log(notify, f"{label} 下载失败，尝试下一个源…", "warn")
    else:
        detail = "；".join(errors[-3:])
        raise FlashError(
            "所有下载源都失败了"
            + (f"（{detail}）" if detail else "")
            + f"。可以手动下载：{source_url}，然后点「浏览」选择文件；"
            + "或在「部署 → 高级选项」里换一个下载源。"
        )

    if official:
        check = test_firmware(str(target), model)
        if not check.ok:
            target.unlink(missing_ok=True)
            raise FlashError(check.error)
        for warning in check.warnings:
            _log(notify, warning, "warn")
        _log(notify, f"官方固件下载完成：{target}", "ok")
        return target

    _log(notify, "正在校验 SHA256…")
    if sha256_file(target) != info.sha256:
        target.unlink(missing_ok=True)
        raise FlashError("固件 SHA256 校验失败，文件可能被篡改或下载不完整，已删除。请重新下载。")

    _log(notify, f"固件下载完成并校验通过：{target}", "ok")
    return target


# ---------------- 业务流程 ----------------


def start_flash_flow(
    update_exe: str,
    image_path: str,
    model: str = "LX06",
    notify: Notify = None,
    timeout: int = 180,
) -> None:
    info = get_model_info(model)

    check = test_firmware(image_path, model)
    for warning in check.warnings:
        _log(notify, warning, "warn")
    if not check.ok:
        raise FlashError(check.error)

    _log(notify, f"机型：{info.name}", "ok")
    _log(notify, f"固件：{Path(image_path).name}（{check.size / 1024 / 1024:.1f} MB）")
    _log(notify, f"请现在拔掉音箱电源，然后再插上（{info.connection}）。", "warn")
    _status(notify, "等待设备… 请重新给音箱上电")

    device = wait_for_device(update_exe, timeout=timeout, notify=notify)
    _log(notify, f"已识别到设备，bootrom 版本：{device.version}", "ok")
    _status(notify, "已识别设备，正在设置启动参数…")

    bulk_cmd(update_exe, "setenv bootdelay 15", notify=notify)
    _log(notify, "√ 启动等待时间已设为 15 秒")

    bulk_cmd(update_exe, "setenv boot_part boot0", notify=notify)
    _log(notify, "√ 启动分区已设为 boot0")

    bulk_cmd(update_exe, "saveenv", notify=notify)
    _log(notify, "√ 启动参数已保存")

    install_system(update_exe, image_path, notify=notify)

    _log(notify, "✅ 刷机完成！请拔掉数据线和电源，等 5 秒后重新上电。", "ok")
    _status(notify, "✅ 刷机完成，请重新上电")


def start_restore_flow(update_exe: str, model: str = "LX06", notify: Notify = None, timeout: int = 180) -> None:
    """切回原厂系统：把启动分区改回 boot1。原厂的 system1 分区没有被修改过。"""
    info = get_model_info(model)
    _log(notify, f"恢复原厂系统（{info.name}）：请现在拔掉音箱电源，然后再插上。", "warn")
    _status(notify, "等待设备… 请重新给音箱上电")

    device = wait_for_device(update_exe, timeout=timeout, notify=notify)
    _log(notify, f"已识别到设备，bootrom 版本：{device.version}", "ok")
    _status(notify, "正在切回原厂系统…")

    bulk_cmd(update_exe, "setenv boot_part boot1", notify=notify)
    bulk_cmd(update_exe, "saveenv", notify=notify)

    _log(notify, "✅ 已切回原厂系统（boot1），请重新上电。", "ok")
    _status(notify, "✅ 已切回原厂系统，请重新上电")


def test_device_flow(update_exe: str, notify: Notify = None, timeout: int = 30) -> Device:
    """只做一次设备识别，用于排查驱动 / 数据线 / 上电时机问题。"""
    _status(notify, "正在识别设备…")
    device = wait_for_device(update_exe, timeout=timeout, notify=notify)
    _log(notify, f"✅ 识别成功，bootrom 版本：{device.version}", "ok")
    _status(notify, "✅ 设备识别成功")
    return device
