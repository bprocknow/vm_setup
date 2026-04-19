from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kernelvm.cli import format_ssh_info
from kernelvm.models import RunMetadata, RuntimeInfo


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
