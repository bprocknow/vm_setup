from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kernelvm.cli import format_ssh_info, start_existing_run
from kernelvm.models import RunMetadata, RuntimeInfo
from kernelvm.runs import save_metadata


class CliReportingTests(unittest.TestCase):
    def test_ssh_info_reports_unavailable_when_readiness_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_root = root / "work" / "run-1"
            for subdir in ("config", "logs", "serial", "cloud-init", "overlay", "artifacts"):
                (run_root / subdir).mkdir(parents=True, exist_ok=True)

            metadata = RunMetadata(
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
                readiness_state="networking-unready",
                readiness_reason="Guest DNS resolution failed during cloud-init package installation.",
                readiness_source="serial-log",
                runtime=RuntimeInfo(serial_log=str(run_root / "serial" / "console.log")),
                errors=[],
            )

            with mock.patch("kernelvm.cli.maybe_detect_ip", return_value=None):
                output = format_ssh_info(metadata)

            self.assertIn("ssh_command: unavailable", output)
            self.assertIn("readiness_state: networking-unready", output)
            self.assertIn("DNS resolution failed", output)

    def test_start_existing_run_clears_stale_network_state_before_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            work_root = root / "work"
            run_root = work_root / "run-1"
            for subdir in ("config", "logs", "serial", "cloud-init", "overlay", "artifacts"):
                (run_root / subdir).mkdir(parents=True, exist_ok=True)
            normalized_config = run_root / "config" / "normalized-config.yaml"
            normalized_config.write_text("{}", encoding="utf-8")

            metadata = RunMetadata(
                run_id="run-1",
                vm_name="kernel-test",
                hostname="kernel-test",
                state="stopped",
                created_at="now",
                updated_at="now",
                config_path=str(root / "config.yaml"),
                normalized_config_path=str(normalized_config),
                base_image_path=str(root / "base.qcow2"),
                overlay_path=str(run_root / "overlay" / "overlay.qcow2"),
                bridge_name="br0",
                mac_address="52:54:00:12:34:56",
                detected_ip="192.168.20.10",
                detected_ip_source="serial-log",
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
                readiness_state="ready",
                readiness_reason=None,
                readiness_source="serial-log",
                runtime=RuntimeInfo(serial_log=str(run_root / "serial" / "console.log")),
                errors=[],
            )
            save_metadata(metadata)

            def fake_start_vm(_config, candidate):
                self.assertIsNone(candidate.detected_ip)
                self.assertIsNone(candidate.detected_ip_source)
                self.assertEqual(candidate.readiness_state, "unknown")
                candidate.state = "running"
                return candidate

            with mock.patch("kernelvm.cli.ensure_single_active_run"), mock.patch(
                "kernelvm.cli.load_config", return_value=object()
            ), mock.patch("kernelvm.cli.start_vm", side_effect=fake_start_vm), mock.patch(
                "kernelvm.cli.assess_network_readiness", side_effect=lambda metadata: metadata
            ):
                restarted = start_existing_run("run-1", work_root)

            self.assertEqual(restarted.state, "running")
