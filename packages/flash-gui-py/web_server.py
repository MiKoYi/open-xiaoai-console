"""本地 HTTP + SSE 桥接层：给 pywebview / 浏览器界面提供 API。"""

from __future__ import annotations

import json
import os
import queue
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import deploy_core as dcore
import flash_core as core
import tooling

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
STATE_PATH = ROOT / "state.json"
GITHUB_URL = "https://github.com/idootop/open-xiaoai"

_pick_lock = threading.Lock()


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


class Hub:
    def __init__(self, root_dir: Path = ROOT) -> None:
        self.root_dir = root_dir
        self.lock = threading.Lock()
        self.busy = False
        self.clients: list[queue.Queue] = []
        self.state = load_state()

    # ---- 事件 ----

    def emit(self, event: dict) -> None:
        for client in list(self.clients):
            client.put(event)

    def notify(self, kind: str, text: str, level: str = "info") -> None:
        if kind == "status":
            self.emit({"type": "status", "text": text})
        else:
            self.emit({"type": "log", "text": text, "level": level})

    def log(self, text: str, level: str = "info") -> None:
        self.emit({"type": "log", "text": text, "level": level})

    def save_state(self, values: dict) -> None:
        self.state.update(values)
        try:
            STATE_PATH.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    # ---- 任务 ----

    def start(self, action: str, params: dict) -> tuple[bool, str]:
        handler = getattr(self, f"_do_{action}", None)
        if handler is None:
            return False, f"未知操作：{action}"
        with self.lock:
            if self.busy:
                return False, "正在执行其他操作，请稍候。"
            self.busy = True
        threading.Thread(target=self._run, args=(action, params), daemon=True).start()
        return True, ""

    def _run(self, action: str, params: dict) -> None:
        try:
            result = getattr(self, f"_do_{action}")(params) or ""
            self.emit({"type": "done", "action": action, "result": result})
        except (core.FlashError, dcore.DeployError) as exc:
            self.emit({"type": "error", "text": str(exc)})
        except Exception as exc:  # noqa: BLE001 - 兜底，避免任务线程静默退出
            self.emit({"type": "error", "text": f"未预期的错误：{exc}"})
        finally:
            with self.lock:
                self.busy = False

    # ---- 刷机 ----

    def _do_flash(self, params: dict) -> str:
        core.start_flash_flow(
            params["exe"], params["image"], params.get("model", "LX06"), self.notify
        )
        return ""

    def _do_restore(self, params: dict) -> str:
        core.start_restore_flow(params["exe"], params.get("model", "LX06"), self.notify)
        return ""

    def _do_identify(self, params: dict) -> str:
        device = core.test_device_flow(params["exe"], self.notify)
        return device.version

    def _do_download(self, params: dict) -> str:
        path = core.download_firmware(self.root_dir / "firmware", params.get("model", "LX06"), self.notify)
        return str(path)

    def _do_find_exe(self, params: dict) -> str:
        return find_update_exe(self.root_dir) or ""

    def _do_install_driver(self, params: dict) -> str:
        installer = find_driver_installer(params.get("exe", ""))
        if not installer:
            raise core.FlashError(
                "没有找到 USB 驱动安装程序。请确认内置工具目录中包含驱动程序，或手动运行刷机工具里的驱动安装程序。"
            )
        os.startfile(installer)  # noqa: S606 - 用户主动点击安装
        return installer

    # ---- 部署 ----

    def _do_deploy(self, params: dict) -> str:
        mode = params["mode"]
        values = params.get("values", {})
        port = int(params.get("port", dcore.DEFAULT_PORT))
        method = params.get("method", "image")
        memory = params.get("memory", "")
        mirror = params.get("mirror", "auto")
        custom_prefix = params.get("mirror_prefix", "")
        image_prefix = params.get("image_prefix", "")
        target = params.get("target", "local")

        if target == "remote":
            ssh = dcore.SshConfig(**params.get("ssh", {}))
            result = dcore.deploy_remote(
                mode,
                values,
                ssh,
                port,
                method=method,
                memory=memory,
                mirror=mirror,
                custom_prefix=custom_prefix,
                image_prefix=image_prefix,
                notify=self.notify,
            )
            check_host = result.host
        else:
            result = dcore.deploy_local(
                mode,
                values,
                port,
                method=method,
                memory=memory,
                mirror=mirror,
                custom_prefix=custom_prefix,
                image_prefix=image_prefix,
                notify=self.notify,
            )
            check_host = "127.0.0.1"

        self.save_state(
            {
                "service_host": check_host,
                "service_port": result.port,
                "service_display": result.url,
                "last_mode": mode,
                "port": result.port,
                "method": method,
                "memory": memory,
            }
        )
        return result.url

    def _do_detect(self, params: dict) -> str:
        return json.dumps(self.check_status(), ensure_ascii=False)

    def check_status(self) -> dict:
        state = self.state
        host = state.get("service_host")
        port = state.get("service_port")
        speaker_ip = state.get("speaker_ip", "")

        service_ok = None
        if host and port:
            service_ok = dcore.tcp_ok(str(host), int(port), 1.5)

        speaker_ok = None
        if speaker_ip:
            speaker_ok = dcore.speaker_online(speaker_ip)

        return {
            "service": service_ok,
            "speaker": speaker_ok,
            "service_display": state.get("service_display", ""),
            "speaker_ip": speaker_ip,
        }


# ---------------- 文件选择 ----------------


def pick_file(kind: str) -> str:
    with _pick_lock:
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            try:
                if kind == "exe":
                    path = filedialog.askopenfilename(
                        title="选择 Amlogic 刷机工具里的 update.exe",
                        filetypes=[("update.exe", "update.exe"), ("可执行文件", "*.exe")],
                    )
                elif kind == "key":
                    path = filedialog.askopenfilename(title="选择 SSH 私钥")
                else:
                    path = filedialog.askopenfilename(
                        title="选择补丁固件（.squashfs）",
                        filetypes=[("Squashfs 固件", "*.squashfs"), ("所有文件", "*.*")],
                    )
            finally:
                root.destroy()
            return path or ""
        except Exception:  # noqa: BLE001 - 没有图形环境时退回手动输入
            return ""


def find_update_exe(root_dir: Path) -> str:
    exe, _source, _zip, _error = tooling.ensure_tool(root_dir)
    if exe:
        return exe

    candidates = [
        root_dir / "Amlogic_Flash_Tool_v6.0.0" / "bin" / "update.exe",
        Path.home() / "Desktop" / "Amlogic_Flash_Tool_v6.0.0" / "bin" / "update.exe",
        Path.home() / "Downloads" / "Amlogic_Flash_Tool_v6.0.0" / "bin" / "update.exe",
    ]
    for path in candidates:
        if path.is_file():
            return str(path)

    for base in (root_dir, Path.home() / "Desktop", Path.home() / "Downloads", Path.home() / "Documents"):
        if not base.is_dir():
            continue
        try:
            for current, dirs, files in __import__("os").walk(base):
                depth = len(Path(current).parts) - len(base.resolve().parts)
                if depth >= 5:
                    dirs[:] = []
                if "update.exe" in [name.lower() for name in files]:
                    path = Path(current) / "update.exe"
                    if "amlogic" in str(path).lower() or "flash" in str(path).lower():
                        return str(path)
        except OSError:
            continue
    return ""


def find_driver_installer(update_exe: str) -> str:
    roots = [ROOT / "tools"]
    if update_exe:
        exe_path = Path(update_exe)
        roots.append(exe_path.parent.parent)
        roots.append(exe_path.parent)

    for root in roots:
        if not root.is_dir():
            continue
        try:
            for current, dirs, files in os.walk(root):
                depth = len(Path(current).parts) - len(root.resolve().parts)
                if depth >= 3:
                    dirs[:] = []
                for name in files:
                    if name.lower().endswith(".exe") and "driver" in name.lower():
                        return str(Path(current) / name)
        except OSError:
            continue
    return ""


# ---------------- 数据 ----------------

def bootstrap_payload(hub: Hub) -> dict:
    try:
        import paramiko  # noqa: F401, PLC0415

        has_paramiko = True
    except ImportError:
        has_paramiko = False

    tool_exe, tool_source, tool_zip, tool_error = tooling.ensure_tool(ROOT)

    return {
        "models": [
            {
                "key": info.model,
                "name": info.name,
                "version": info.version,
                "connection": info.connection,
                "filename": info.filename,
            }
            for info in core.MODELS.values()
        ],
        "modes": [
            {
                "key": spec.key,
                "title": spec.title,
                "tagline": spec.tagline,
                "note": spec.note,
                "deployable": spec.deployable,
                "image": spec.image,
                "config_name": spec.config_name,
                "fields": [asdict(field) for field in spec.fields],
            }
            for spec in dcore.MODES.values()
        ],
        "examples": [
            {
                "key": entry.key,
                "title": entry.spec.title if entry.spec else entry.title,
                "mode": entry.spec.key if entry.spec else None,
                "deployable": bool(entry.spec and entry.spec.deployable),
            }
            for entry in dcore.discover_examples(dcore.REPO_ROOT)
        ],
        "settings": hub.state,
        "defaults": {
            "model": "LX06",
            "mode": "xiaozhi",
            "port": dcore.DEFAULT_PORT,
            "mirror": "auto",
        },
        "mirrors": [
            {"key": key, "label": label}
            for key, (label, _prefix) in dcore.MIRROR_PRESETS.items()
        ],
        "tutorial_url": core.TUTORIAL_URL,
        "tool_url": core.TOOL_URL,
        "github_url": GITHUB_URL,
        "bundled_exe": tool_exe,
        "tool_source": tool_source,
        "tool_zip": tool_zip,
        "tool_error": tool_error,
        "paramiko": has_paramiko,
    }


def make_handler(hub: Hub):
    class Handler(BaseHTTPRequestHandler):
        server_version = "OpenXiaoAIConsole/1.0"

        def log_message(self, fmt, *args):  # noqa: A003 - 关闭访问日志
            return

        # ---- 工具 ----

        def _send_json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                pass

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return {}

        def _serve_file(self, name: str, content_type: str) -> None:
            path = WEB_DIR / name
            if not path.is_file():
                self.send_error(404)
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                pass

        # ---- GET ----

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._serve_file("index.html", "text/html; charset=utf-8")
            elif path == "/style.css":
                self._serve_file("style.css", "text/css; charset=utf-8")
            elif path == "/app.js":
                self._serve_file("app.js", "application/javascript; charset=utf-8")
            elif path == "/api/bootstrap":
                self._send_json(bootstrap_payload(hub))
            elif path == "/api/status":
                self._send_json(hub.check_status())
            elif path == "/api/events":
                self._stream_events()
            else:
                self.send_error(404)

        def _stream_events(self) -> None:
            client: queue.Queue = queue.Queue()
            hub.clients.append(client)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while True:
                    try:
                        event = client.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    data = json.dumps(event, ensure_ascii=False)
                    self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                if client in hub.clients:
                    hub.clients.remove(client)

        # ---- POST ----

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            payload = self._read_json()

            if path == "/api/start":
                action = payload.pop("action", "")
                ok, message = hub.start(action, payload)
                self._send_json({"ok": ok, "message": message}, 200 if ok else 409)
            elif path == "/api/settings":
                hub.save_state(payload)
                self._send_json({"ok": True})
            elif path == "/api/pick":
                self._send_json({"path": pick_file(payload.get("kind", "image"))})
            else:
                self.send_error(404)

    return Handler


def create_server(port: int = 0, hub: Hub | None = None) -> tuple[ThreadingHTTPServer, Hub]:
    hub = hub or Hub()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(hub))
    return httpd, hub


def serve_background(port: int = 0) -> tuple[ThreadingHTTPServer, Hub]:
    httpd, hub = create_server(port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, hub
