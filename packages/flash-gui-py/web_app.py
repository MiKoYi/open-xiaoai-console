"""桌面窗口入口：启动本地服务，用 pywebview 打开 HTML 界面。

没有 pywebview 时退回系统浏览器，方便调试。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from web_server import serve_background


def wait_browser(url: str) -> None:
    webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def center_position(width: int, height: int) -> tuple[int | None, int | None]:
    if os.name != "nt":
        return None, None
    try:
        user32 = ctypes.windll.user32
        screen_width = user32.GetSystemMetrics(0)
        screen_height = user32.GetSystemMetrics(1)
        if screen_width and screen_height:
            return max(0, (screen_width - width) // 2), max(0, (screen_height - height) // 2)
    except Exception:  # noqa: BLE001 - 取屏幕尺寸失败就交给系统
        pass
    return None, None


def bring_to_front(window, icon_path: Path) -> None:
    """等窗口创建后设置图标并置前。"""
    if os.name != "nt":
        return
    try:
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        user32.SendMessageW.restype = ctypes.c_void_p
        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        user32.SetWindowPos.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]

        native = getattr(window, "native", None)
        hwnd = 0
        if native is not None:
            try:
                hwnd = int(native.Handle)
            except Exception:  # noqa: BLE001 - 拿不到原生句柄就走 FindWindow
                hwnd = 0
        if not hwnd:
            deadline = time.time() + 8
            while time.time() < deadline:
                hwnd = user32.FindWindowW(None, "Open-XiaoAI 控制台")
                if hwnd:
                    break
                time.sleep(0.2)
        if not hwnd:
            return

        if icon_path.is_file() and native is not None:
            try:
                from System.Drawing import Icon  # type: ignore[import-not-found]

                native.Icon = Icon(str(icon_path))
            except Exception:  # noqa: BLE001 - pythonnet 不可用时退回 Win32
                pass

        if icon_path.is_file():
            user32.LoadImageW.restype = ctypes.c_void_p
            user32.LoadImageW.argtypes = [
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                ctypes.c_uint,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint,
            ]
            big_w = user32.GetSystemMetrics(11)
            big_h = user32.GetSystemMetrics(12)
            small_w = user32.GetSystemMetrics(49)
            small_h = user32.GetSystemMetrics(50)
            hicon_big = user32.LoadImageW(None, str(icon_path), 1, big_w, big_h, 0x10)
            hicon_small = user32.LoadImageW(None, str(icon_path), 1, small_w, small_h, 0x10)
            if hicon_big:
                user32.SendMessageW(ctypes.c_void_p(hwnd), 0x80, 1, ctypes.c_void_p(hicon_big))
            if hicon_small:
                user32.SendMessageW(ctypes.c_void_p(hwnd), 0x80, 0, ctypes.c_void_p(hicon_small))

        user32.ShowWindow(ctypes.c_void_p(hwnd), 9)
        user32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0001 | 0x0002)
        user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
        time.sleep(1.0)
        user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
        user32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(-2), 0, 0, 0, 0, 0x0001 | 0x0002)
    except Exception:  # noqa: BLE001 - 置前失败不影响使用
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open-XiaoAI 控制台")
    parser.add_argument("--browser", action="store_true", help="强制用系统浏览器打开")
    parser.add_argument("--selftest", action="store_true", help="只启动服务并检查接口")
    parser.add_argument("--debug", action="store_true", help="打开 pywebview 调试")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    httpd, _hub = serve_background(0)
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/"

    if args.selftest:
        try:
            with urllib.request.urlopen(url + "api/bootstrap", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            assert payload["models"] and payload["modes"]
            print("SELFTEST OK")
            return 0
        finally:
            httpd.shutdown()

    if args.browser:
        print(f"控制台地址：{url}")
        try:
            wait_browser(url)
        finally:
            httpd.shutdown()
        return 0

    try:
        import webview
    except ImportError:
        print("缺少 pywebview，已改用浏览器打开。")
        try:
            wait_browser(url)
        finally:
            httpd.shutdown()
        return 0

    x, y = center_position(1180, 860)
    icon_path = Path(__file__).with_name("web") / "icon.ico"
    try:
        window = webview.create_window(
            "Open-XiaoAI 控制台",
            url,
            width=1180,
            height=860,
            min_size=(980, 700),
            x=x,
            y=y,
            background_color="#F5F6F8",
        )
    except TypeError:
        window = webview.create_window(
            "Open-XiaoAI 控制台",
            url,
            width=1180,
            height=860,
            x=x,
            y=y,
        )

    try:
        webview.start(lambda: bring_to_front(window, icon_path), debug=args.debug, icon=str(icon_path))
    finally:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
