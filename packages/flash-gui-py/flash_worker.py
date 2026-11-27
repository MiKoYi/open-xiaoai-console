"""在管理员权限下执行刷机流程，用 JSONL 事件文件把结果回传给控制台。

控制台界面（WebView2）不能以管理员权限运行，所以刷机时由 flash_core 通过
ShellExecuteEx(runas) 拉起本脚本；本脚本只负责跑 flash_core 的流程，
把 notify 事件写成一行一个 JSON，父进程边读边显示到界面上。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import flash_core


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open-XiaoAI 刷机子进程")
    parser.add_argument("--action", required=True, choices=("flash", "restore", "identify"))
    parser.add_argument("--exe", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--image", default="")
    parser.add_argument("--model", default="LX06")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)

    events = Path(args.events)
    with events.open("w", encoding="utf-8") as handle:

        def notify(kind: str, text: str, level: str = "") -> None:
            event: dict = {"type": kind, "text": text}
            if level:
                event["level"] = level
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            handle.flush()

        def emit(event: dict) -> None:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            handle.flush()

        try:
            if args.action == "flash":
                flash_core.start_flash_flow(
                    args.exe, args.image, args.model, notify=notify, timeout=args.timeout
                )
            elif args.action == "restore":
                flash_core.start_restore_flow(args.exe, args.model, notify=notify, timeout=args.timeout)
            else:
                device = flash_core.test_device_flow(args.exe, notify=notify, timeout=args.timeout)
                emit({"type": "result", "version": device.version})
        except flash_core.FlashError as exc:
            emit({"type": "error", "text": str(exc)})
            return 1
        except Exception as exc:  # noqa: BLE001 - 兜底，让父进程能显示原因
            emit({"type": "error", "text": f"未预期的错误：{exc}"})
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
