from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kernelvm.host_network import diagnose_bridge


class HostBridgeDiagnosticsTests(unittest.TestCase):
    def test_diagnose_bridge_reports_down_bridge_without_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sys_class_net = Path(tmpdir) / "sys" / "class" / "net"
            bridge_path = sys_class_net / "br0"
            (bridge_path / "bridge").mkdir(parents=True)
            (bridge_path / "brif").mkdir()
            (bridge_path / "operstate").write_text("down\n", encoding="utf-8")
            (bridge_path / "carrier").write_text("0\n", encoding="utf-8")

            diagnosis = diagnose_bridge("br0", sys_class_net=sys_class_net)

            self.assertIn("down and has no attached interfaces", diagnosis or "")

    def test_diagnose_bridge_allows_up_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sys_class_net = Path(tmpdir) / "sys" / "class" / "net"
            bridge_path = sys_class_net / "br0"
            (bridge_path / "bridge").mkdir(parents=True)
            (bridge_path / "brif").mkdir()
            (bridge_path / "operstate").write_text("up\n", encoding="utf-8")
            (bridge_path / "carrier").write_text("1\n", encoding="utf-8")

            diagnosis = diagnose_bridge("br0", sys_class_net=sys_class_net)

            self.assertIsNone(diagnosis)
