"""状态检测工具测试。

只需要本机开一个临时端口，不依赖音箱或 Docker。
"""

from __future__ import annotations

import socket
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deploy_core import tcp_ok as _tcp_ok  # noqa: E402


class TcpCheckTests(unittest.TestCase):
    def test_open_port_is_online(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]
            self.assertTrue(_tcp_ok("127.0.0.1", port, 1.0))

    def test_closed_port_is_offline(self) -> None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        self.assertFalse(_tcp_ok("127.0.0.1", port, 0.5))

    def test_invalid_host_is_offline(self) -> None:
        self.assertFalse(_tcp_ok("127.0.0.1", 1, 0.2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
