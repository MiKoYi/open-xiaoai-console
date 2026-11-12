"""桌面窗口入口：启动本地服务，用 pywebview 打开 HTML 界面。

没有 pywebview 时退回系统浏览器，方便调试。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import webbrowser

from web_server import serve_background


def wait_browser(url: str) -> None:
    webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
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

    try:
        webview.create_window(
            "Open-XiaoAI 控制台",
            url,
            width=1180,
            height=860,
            min_size=(980, 700),
        )
    except TypeError:
        webview.create_window("Open-XiaoAI 控制台", url, width=1180, height=860)

    try:
        webview.start(debug=args.debug)
    finally:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
