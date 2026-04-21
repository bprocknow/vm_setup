from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kernelvm.cli import format_ssh_info, start_existing_run
from kernelvm.errors import ValidationError
from kernelvm.models import RunMetadata, RuntimeInfo
from kernelvm.runs import load_metadata, save_metadata


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

    def test_start_existing_run_reuses_normalized_config_for_gdb_debug_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_root = root / "work" / "run-1"
            for subdir in ("config", "logs", "serial", "cloud-init", "overlay", "artifacts"):
                (run_root / subdir).mkdir(parents=True, exist_ok=True)

            base_image = root / "base.qcow2"
            kernel_image = root / "bzImage"
            modules_archive = root / "modules.tar.zst"
            for path in (base_image, kernel_image, modules_archive):
                path.write_text("x", encoding="utf-8")

            normalized_config = run_root / "config" / "normalized-config.yaml"
            normalized_config.write_text(
                f"""
vm_name: kernel-test
base_image_path: {base_image}
vcpus: 2
memory_mb: 2048
bridge_name: br0
qemu_gdb_debug: true
root_ssh_authorized_keys:
  - ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockKey comment
kernel_artifacts:
  kernel_image: {kernel_image}
  kernel_modules_archive: {modules_archive}
kernel_cmdline_append:
  - root=/dev/vda3
""",
                encoding="utf-8",
            )

            metadata = RunMetadata(
                run_id="run-1",
                vm_name="kernel-test",
                hostname="kernel-test",
                state="stopped",
                created_at="now",
                updated_at="now",
                config_path=str(root / "config.yaml"),
                normalized_config_path=str(normalized_config),
                base_image_path=str(base_image),
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
                runtime=RuntimeInfo(),
                errors=[],
            )
            save_metadata(metadata)

            def fake_start_vm(config, loaded_metadata):
                self.assertTrue(config.qemu_gdb_debug)
                return loaded_metadata

            with (
                mock.patch("kernelvm.cli.start_vm", side_effect=fake_start_vm),
                mock.patch("kernelvm.cli.assess_network_readiness", side_effect=lambda item: item),
            ):
                start_existing_run("run-1", root / "work")

    def test_start_existing_run_marks_run_failed_when_direct_boot_config_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_root = root / "work" / "run-1"
            for subdir in ("config", "logs", "serial", "cloud-init", "overlay", "artifacts"):
                (run_root / subdir).mkdir(parents=True, exist_ok=True)

            base_image = root / "base.qcow2"
            kernel_image = root / "bzImage"
            modules_archive = root / "modules.tar.zst"
            for path in (base_image, kernel_image, modules_archive):
                path.write_text("x", encoding="utf-8")

            normalized_config = run_root / "config" / "normalized-config.yaml"
            normalized_config.write_text(
                f"""
vm_name: kernel-test
base_image_path: {base_image}
vcpus: 2
memory_mb: 2048
bridge_name: br0
root_ssh_authorized_keys:
  - ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockKey comment
kernel_artifacts:
  kernel_image: {kernel_image}
  kernel_modules_archive: {modules_archive}
kernel_cmdline_append:
  - console=ttyS0,115200n8
""",
                encoding="utf-8",
            )

            metadata = RunMetadata(
                run_id="run-1",
                vm_name="kernel-test",
                hostname="kernel-test",
                state="stopped",
                created_at="now",
                updated_at="now",
                config_path=str(root / "config.yaml"),
                normalized_config_path=str(normalized_config),
                base_image_path=str(base_image),
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
                runtime=RuntimeInfo(),
                errors=[],
            )
            save_metadata(metadata)

            with self.assertRaises(ValidationError):
                start_existing_run("run-1", root / "work")

            failed_metadata = load_metadata(root / "work", "run-1")
            self.assertEqual(failed_metadata.state, "failed")
            self.assertIn(
                "kernel_cmdline_append must include a root=... entry when kernel_artifacts.kernel_image is configured",
                failed_metadata.errors,
            )
