from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kernelvm.models import KernelArtifacts, RunMetadata, RuntimeInfo, VMConfig
from kernelvm.qemu import build_qemu_command


class QemuCommandTests(unittest.TestCase):
    def test_qemu_command_prefers_payload_image_over_legacy_dir_share(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            metadata = RunMetadata(
                run_id="run-1",
                vm_name="kernel-test",
                hostname="kernel-test",
                state="created",
                created_at="now",
                updated_at="now",
                config_path=str(root / "config.yaml"),
                normalized_config_path=str(root / "work" / "run-1" / "config" / "normalized-config.yaml"),
                base_image_path=str(root / "base.qcow2"),
                overlay_path=str(root / "work" / "run-1" / "overlay" / "overlay.qcow2"),
                bridge_name="br0",
                mac_address="52:54:00:12:34:56",
                detected_ip=None,
                disk_bus="virtio",
                net_model="virtio",
                vcpus=2,
                memory_mb=2048,
                disk_size_gb=20,
                paths={
                    "root": str(root / "work" / "run-1"),
                    "config_dir": str(root / "work" / "run-1" / "config"),
                    "logs_dir": str(root / "work" / "run-1" / "logs"),
                    "serial_dir": str(root / "work" / "run-1" / "serial"),
                    "cloud_init_dir": str(root / "work" / "run-1" / "cloud-init"),
                    "overlay_dir": str(root / "work" / "run-1" / "overlay"),
                    "artifacts_dir": str(root / "work" / "run-1" / "artifacts"),
                },
                kernel_artifacts={},
                runtime=RuntimeInfo(
                    seed_image=str(root / "work" / "run-1" / "cloud-init" / "seed.img"),
                    payload_dir=str(root / "work" / "run-1" / "artifacts" / "payload"),
                    payload_image=str(root / "work" / "run-1" / "artifacts" / "payload.img"),
                    payload_filesystem="ext4",
                    payload_label="KERNELVMPAYLOAD",
                ),
                errors=[],
            )
            config = VMConfig(
                base_image_path=root / "base.qcow2",
                vm_name="kernel-test",
                vm_name_prefix=None,
                vcpus=2,
                memory_mb=2048,
                disk_size_gb=20,
                bridge_name="br0",
                kernel_artifacts=KernelArtifacts(
                    kernel_image=root / "bzImage",
                    kernel_modules_archive=root / "modules.tar.zst",
                ),
                root_ssh_authorized_keys=["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockKey comment"],
            )

            with mock.patch("kernelvm.qemu.ensure_command", return_value="qemu-system-x86_64"):
                command = build_qemu_command(config, metadata)

            command_string = " ".join(command)
            self.assertIn("bridge,br=br0", command_string)
            self.assertIn("overlay.qcow2", command_string)
            self.assertIn("console.sock", command_string)
            self.assertIn("payload.img", command_string)
            self.assertNotIn("fat:ro:", command_string)

    def test_qemu_command_falls_back_to_legacy_payload_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            metadata = RunMetadata(
                run_id="run-1",
                vm_name="kernel-test",
                hostname="kernel-test",
                state="created",
                created_at="now",
                updated_at="now",
                config_path=str(root / "config.yaml"),
                normalized_config_path=str(root / "work" / "run-1" / "config" / "normalized-config.yaml"),
                base_image_path=str(root / "base.qcow2"),
                overlay_path=str(root / "work" / "run-1" / "overlay" / "overlay.qcow2"),
                bridge_name="br0",
                mac_address="52:54:00:12:34:56",
                detected_ip=None,
                disk_bus="virtio",
                net_model="virtio",
                vcpus=2,
                memory_mb=2048,
                disk_size_gb=20,
                paths={
                    "root": str(root / "work" / "run-1"),
                    "config_dir": str(root / "work" / "run-1" / "config"),
                    "logs_dir": str(root / "work" / "run-1" / "logs"),
                    "serial_dir": str(root / "work" / "run-1" / "serial"),
                    "cloud_init_dir": str(root / "work" / "run-1" / "cloud-init"),
                    "overlay_dir": str(root / "work" / "run-1" / "overlay"),
                    "artifacts_dir": str(root / "work" / "run-1" / "artifacts"),
                },
                kernel_artifacts={},
                runtime=RuntimeInfo(
                    seed_image=str(root / "work" / "run-1" / "cloud-init" / "seed.img"),
                    payload_dir=str(root / "work" / "run-1" / "artifacts" / "payload"),
                ),
                errors=[],
            )
            config = VMConfig(
                base_image_path=root / "base.qcow2",
                vm_name="kernel-test",
                vm_name_prefix=None,
                vcpus=2,
                memory_mb=2048,
                disk_size_gb=20,
                bridge_name="br0",
                kernel_artifacts=KernelArtifacts(
                    kernel_image=root / "bzImage",
                    kernel_modules_archive=root / "modules.tar.zst",
                ),
                root_ssh_authorized_keys=["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockKey comment"],
            )

            with mock.patch("kernelvm.qemu.ensure_command", return_value="qemu-system-x86_64"):
                command = build_qemu_command(config, metadata)

            self.assertIn("fat:ro:", " ".join(command))
