from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from kernelvm.models import RunMetadata, RuntimeInfo
from kernelvm.network import (
    HOST_NEIGH_SOURCE,
    QEMU_PROCESS_SOURCE,
    READINESS_READY,
    READINESS_UNREADY,
    assess_network_readiness,
    detect_ip_for_mac,
    inspect_serial_log,
    maybe_detect_ip,
)


class NetworkReadinessTests(unittest.TestCase):
    def _metadata(self, root: Path, *, serial_log: Path | None = None) -> RunMetadata:
        run_root = root / "work" / "run-1"
        for subdir in ("config", "logs", "serial", "cloud-init", "overlay", "artifacts"):
            (run_root / subdir).mkdir(parents=True, exist_ok=True)
        return RunMetadata(
            run_id="run-1",
            vm_name="kernel-test",
            hostname="kernel-test",
            state="running",
            created_at="now",
            updated_at="now",
            config_path=str(root / "config.yaml"),
            normalized_config_path=str(run_root / "config" / "normalized-config.yaml"),
            base_image_path=str(root / "base.qcow2"),
            overlay_path=str(run_root / "overlay" / "overlay.qcow2"),
            bridge_name="br0",
            mac_address="52:54:00:12:34:56",
            detected_ip=None,
            disk_bus="virtio",
            net_model="virtio",
            vcpus=2,
            memory_mb=2048,
            disk_size_gb=20,
            paths={
                "root": str(run_root),
                "config_dir": str(run_root / "config"),
                "logs_dir": str(run_root / "logs"),
                "serial_dir": str(run_root / "serial"),
                "cloud_init_dir": str(run_root / "cloud-init"),
                "overlay_dir": str(run_root / "overlay"),
                "artifacts_dir": str(run_root / "artifacts"),
            },
            kernel_artifacts={},
            runtime=RuntimeInfo(serial_log=str(serial_log) if serial_log else None),
            errors=[],
        )

    def test_detect_ip_for_mac_returns_ip_with_source(self) -> None:
        payload = '[{"dst":"192.168.122.50","lladdr":"52:54:00:12:34:56"}]'
        with mock.patch("kernelvm.network.run_command", return_value=SimpleNamespace(stdout=payload)):
            detected_ip, source = detect_ip_for_mac("52:54:00:12:34:56")

        self.assertEqual(detected_ip, "192.168.122.50")
        self.assertEqual(source, HOST_NEIGH_SOURCE)

    def test_inspect_serial_log_classifies_no_ipv4_dns_failure_fixture(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "network" / "no_ipv4_console.log"
        observation = inspect_serial_log(fixture)

        self.assertIsNone(observation.detected_ip)
        self.assertIn("DNS resolution failed", observation.readiness_reason or "")
        self.assertTrue(observation.ssh_ready)

    def test_inspect_serial_log_classifies_initramfs_init_handoff_stall(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "console.log"
            path.write_text(
                "\n".join(
                    [
                        "[   10.388637] Unpacking initramfs...",
                        "[   11.794217] Freeing initrd memory: 28400K",
                        "[   13.387354] Run /init as init process",
                    ]
                ),
                encoding="utf-8",
            )

            observation = inspect_serial_log(path)

        self.assertIsNone(observation.detected_ip)
        self.assertIn("kernel started /init", observation.readiness_reason or "")

    def test_assess_network_readiness_marks_run_ready_when_ip_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            metadata = self._metadata(root)

            with mock.patch("kernelvm.network.detect_ip_for_mac", return_value=("192.168.122.50", HOST_NEIGH_SOURCE)):
                assess_network_readiness(metadata, timeout_seconds=0, poll_interval_seconds=0)

            self.assertEqual(metadata.detected_ip, "192.168.122.50")
            self.assertEqual(metadata.detected_ip_source, HOST_NEIGH_SOURCE)
            self.assertEqual(metadata.readiness_state, READINESS_READY)

    def test_maybe_detect_ip_records_networking_unready_reason_from_serial_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            serial_log = root / "console.log"
            serial_log.write_text((Path(__file__).parent / "fixtures" / "network" / "no_ipv4_console.log").read_text(encoding="utf-8"), encoding="utf-8")
            metadata = self._metadata(root, serial_log=serial_log)

            with mock.patch("kernelvm.network.diagnose_bridge", return_value=None):
                detected_ip = maybe_detect_ip(metadata)

            self.assertIsNone(detected_ip)
            self.assertEqual(metadata.readiness_state, READINESS_UNREADY)
            self.assertIn("DNS resolution failed", metadata.readiness_reason or "")

    def test_maybe_detect_ip_prefers_host_bridge_diagnosis_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            serial_log = root / "console.log"
            serial_log.write_text((Path(__file__).parent / "fixtures" / "network" / "no_ipv4_console.log").read_text(encoding="utf-8"), encoding="utf-8")
            metadata = self._metadata(root, serial_log=serial_log)

            with mock.patch(
                "kernelvm.network.diagnose_bridge",
                return_value="Host bridge br0 is down and has no attached interfaces. Move the host uplink and IP configuration onto the bridge before launching the VM.",
            ):
                detected_ip = maybe_detect_ip(metadata)

            self.assertIsNone(detected_ip)
            self.assertEqual(metadata.readiness_state, READINESS_UNREADY)
            self.assertIn("Host bridge br0 is down", metadata.readiness_reason or "")
            self.assertEqual(metadata.readiness_source, "host-bridge-check")

    def test_assess_network_readiness_times_out_without_ip_or_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            metadata = self._metadata(root)

            with mock.patch("kernelvm.network.detect_ip_for_mac", return_value=(None, None)), mock.patch(
                "kernelvm.network.diagnose_bridge", return_value=None
            ):
                assess_network_readiness(metadata, timeout_seconds=0, poll_interval_seconds=0)

            self.assertEqual(metadata.readiness_state, READINESS_UNREADY)
            self.assertIn("No usable guest IPv4 address", metadata.readiness_reason or "")

    def test_assess_network_readiness_reports_qemu_exit_with_serial_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            serial_log = root / "console.log"
            serial_log.write_text(
                "\n".join(
                    [
                        "[   10.388637] Unpacking initramfs...",
                        "[   11.794217] Freeing initrd memory: 28400K",
                        "[   13.387354] Run /init as init process",
                    ]
                ),
                encoding="utf-8",
            )
            metadata = self._metadata(root, serial_log=serial_log)
            metadata.runtime.pid = 12345

            with (
                mock.patch("kernelvm.network.detect_ip_for_mac", return_value=(None, None)),
                mock.patch("kernelvm.network.diagnose_bridge", return_value=None),
                mock.patch("kernelvm.network._pid_is_running", return_value=False),
            ):
                assess_network_readiness(metadata, timeout_seconds=90, poll_interval_seconds=0)

            self.assertEqual(metadata.state, "stopped")
            self.assertIsNone(metadata.runtime.pid)
            self.assertEqual(metadata.readiness_state, READINESS_UNREADY)
            self.assertEqual(metadata.readiness_source, QEMU_PROCESS_SOURCE)
            self.assertIn("QEMU exited", metadata.readiness_reason or "")
            self.assertIn("kernel started /init", metadata.readiness_reason or "")

    def test_inspect_serial_log_ignores_unreadable_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "console.log"
            path.write_text("x", encoding="utf-8")

            with mock.patch.object(Path, "read_text", side_effect=PermissionError("denied")):
                observation = inspect_serial_log(path)

            self.assertIsNone(observation.detected_ip)
            self.assertIsNone(observation.readiness_reason)
