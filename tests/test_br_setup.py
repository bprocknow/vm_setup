from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "br_setup"


class BridgeSetupScriptTests(unittest.TestCase):
    def test_br_setup_configures_bridge_and_runs_sanity_test(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fakebin = root / "fakebin"
            fakebin.mkdir()
            state_dir = root / "state"
            state_dir.mkdir()
            sys_class_net = root / "sys" / "class" / "net"
            (sys_class_net / "eno1").mkdir(parents=True)
            qemu_dir = root / "etc" / "qemu"

            self._write_executable(fakebin / "nmcli", self._fake_nmcli())
            self._write_executable(fakebin / "ip", self._fake_ip())
            self._write_executable(fakebin / "bridge", self._fake_bridge())
            self._write_executable(fakebin / "ping", self._fake_ping())

            env = os.environ.copy()
            env["PATH"] = f"{fakebin}:{env['PATH']}"
            env["BR_SETUP_SKIP_ROOT_CHECK"] = "1"
            env["BR_SETUP_SYS_CLASS_NET"] = str(sys_class_net)
            env["BR_SETUP_QEMU_DIR"] = str(qemu_dir)
            env["BR_SETUP_TEST_STATE_DIR"] = str(state_dir)

            result = subprocess.run(
                [str(SCRIPT_PATH), "eno1"],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("Bridge sanity test passed for br0 on eno1", result.stderr)
            self.assertIn("default via 192.168.20.1 dev br0", result.stdout)
            self.assertEqual((sys_class_net / "br0" / "operstate").read_text(encoding="utf-8"), "up\n")
            self.assertTrue((sys_class_net / "br0" / "brif" / "eno1").exists())
            self.assertIn("allow br0", (qemu_dir / "bridge.conf").read_text(encoding="utf-8"))

    def test_br_setup_help(self) -> None:
        result = subprocess.run(
            [str(SCRIPT_PATH), "--help"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Usage: br_setup", result.stdout)

    def _write_executable(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _fake_nmcli(self) -> str:
        return textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import os
            import sys
            from pathlib import Path

            state_dir = Path(os.environ["BR_SETUP_TEST_STATE_DIR"])
            sys_class_net = Path(os.environ["BR_SETUP_SYS_CLASS_NET"])
            connections_dir = state_dir / "connections"
            connections_dir.mkdir(exist_ok=True)

            args = sys.argv[1:]
            if args[:2] == ["connection", "show"] and len(args) == 3:
                path = connections_dir / args[2]
                sys.exit(0 if path.exists() else 10)

            if args[:2] == ["connection", "add"]:
                if "type" in args:
                    conn_type = args[args.index("type") + 1]
                else:
                    conn_type = ""
                conn_name = args[args.index("con-name") + 1]
                ifname = args[args.index("ifname") + 1]
                (connections_dir / conn_name).write_text(conn_type + "\\n", encoding="utf-8")
                if conn_type == "bridge":
                    bridge_root = sys_class_net / ifname
                    (bridge_root / "bridge").mkdir(parents=True, exist_ok=True)
                    (bridge_root / "brif").mkdir(exist_ok=True)
                    (bridge_root / "operstate").write_text("down\\n", encoding="utf-8")
                    (bridge_root / "carrier").write_text("0\\n", encoding="utf-8")
                elif conn_type == "bridge-slave":
                    (state_dir / "slave_interface").write_text(ifname + "\\n", encoding="utf-8")
                    bridge_name = args[args.index("master") + 1]
                    (state_dir / "bridge_name").write_text(bridge_name + "\\n", encoding="utf-8")
                sys.exit(0)

            if args[:2] == ["connection", "modify"]:
                sys.exit(0)

            if args[:2] == ["connection", "up"] and len(args) == 3:
                bridge_name = args[2]
                bridge_root = sys_class_net / bridge_name
                bridge_root.mkdir(parents=True, exist_ok=True)
                (bridge_root / "bridge").mkdir(exist_ok=True)
                (bridge_root / "brif").mkdir(exist_ok=True)
                (bridge_root / "operstate").write_text("up\\n", encoding="utf-8")
                (bridge_root / "carrier").write_text("1\\n", encoding="utf-8")
                interface_name = (state_dir / "slave_interface").read_text(encoding="utf-8").strip()
                (bridge_root / "brif" / interface_name).mkdir(exist_ok=True)
                (state_dir / "default_route").write_text(
                    "default via 192.168.20.1 dev " + bridge_name + " proto dhcp src 192.168.20.2 metric 100\\n",
                    encoding="utf-8",
                )
                sys.exit(0)

            raise SystemExit(f"unexpected nmcli arguments: {args}")
            """
        )

    def _fake_ip(self) -> str:
        return textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import os
            import sys
            from pathlib import Path

            state_dir = Path(os.environ["BR_SETUP_TEST_STATE_DIR"])
            sys_class_net = Path(os.environ["BR_SETUP_SYS_CLASS_NET"])
            args = sys.argv[1:]

            if args[:4] == ["-brief", "link", "show", "br0"]:
                operstate = (sys_class_net / "br0" / "operstate").read_text(encoding="utf-8").strip()
                print(f"br0             {operstate.upper()}")
                sys.exit(0)

            if args[:3] == ["route", "show", "default"]:
                route_file = state_dir / "default_route"
                if route_file.exists():
                    print(route_file.read_text(encoding="utf-8"), end="")
                sys.exit(0)

            raise SystemExit(f"unexpected ip arguments: {args}")
            """
        )

    def _fake_bridge(self) -> str:
        return textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import os
            import sys
            from pathlib import Path

            sys_class_net = Path(os.environ["BR_SETUP_SYS_CLASS_NET"])
            args = sys.argv[1:]
            if args[:4] == ["link", "show", "master", "br0"]:
                bridge_members = sys_class_net / "br0" / "brif"
                if bridge_members.exists():
                    for member in sorted(bridge_members.iterdir()):
                        print(f"7: {member.name}: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 master br0 state forwarding")
                sys.exit(0)

            raise SystemExit(f"unexpected bridge arguments: {args}")
            """
        )

    def _fake_ping(self) -> str:
        return textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import sys

            args = sys.argv[1:]
            if args[:4] == ["-c", "1", "-W", "2"]:
                sys.exit(0)
            raise SystemExit(f"unexpected ping arguments: {args}")
            """
        )
