"""模拟的 Amlogic update.exe（Python 版），仅用于自动化测试，不会碰真实设备。

行为由环境变量控制：
    FAKE_IDENTIFY_AFTER  第几次 identify 开始成功（默认 2）
    FAKE_MODE            fail-bulk / fail-partition
"""

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    with (HERE / "calls.log").open("a", encoding="utf-8") as handle:
        handle.write(" ".join(sys.argv[1:]) + "\n")

    if len(sys.argv) < 2:
        return 2

    command = sys.argv[1].lower()
    mode = os.environ.get("FAKE_MODE", "")

    if command == "identify":
        counter = HERE / "identify.count"
        attempts = int(counter.read_text(encoding="utf-8")) if counter.is_file() else 0
        attempts += 1
        counter.write_text(str(attempts), encoding="utf-8")
        need = int(os.environ.get("FAKE_IDENTIFY_AFTER", "2"))

        print("AmlUsbIdentifyHost")
        if attempts < need:
            print("can not find device")
            print("ERR: AmlUsbIdentifyHost failed!", file=sys.stderr)
            return 1
        print("This firmware version is 0-7-0-16-0-0-0-0")
        return 0

    if command == "bulkcmd":
        argument = sys.argv[2] if len(sys.argv) > 2 else ""
        print(f"AmlUsbBulkCmd = {argument}")
        if mode == "fail-bulk":
            print("Read status return failed:")
            print("strncmp != 0")
            print("ERR: AmlUsbBulkCmd failed!")
            return 1
        print("Read status return success")
        return 0

    if command == "partition":
        time.sleep(0.2)
        print("file size is 0x250d000")
        print("Downloading....")
        time.sleep(0.4)
        print("Transfer size 0x250d000B(37MB)")
        if mode == "fail-partition":
            print("Usb status not success")
            return 1
        print("[update]:mwrite success")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
