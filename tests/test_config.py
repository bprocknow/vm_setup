from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kernelvm.config import load_config
from kernelvm.errors import ValidationError


class ConfigValidationTests(unittest.TestCase):
    def test_load_valid_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base_image = root / "base.qcow2"
            kernel_image = root / "bzImage"
            modules = root / "modules.tar.zst"
            for path in (base_image, kernel_image, modules):
                path.write_text("x", encoding="utf-8")

            config_path = root / "config.yaml"
            config_path.write_text(
                f"""
vm_name: kernel-test
base_image_path: {base_image}
vcpus: 4
memory_mb: 8192
disk_size_gb: 40
bridge_name: br0
root_ssh_authorized_keys:
  - ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockKey comment
kernel_artifacts:
  kernel_image: {kernel_image}
  kernel_modules_archive: {modules}
kernel_cmdline_append:
  - root=/dev/vda3
""",
                encoding="utf-8",
            )

            config = load_config(config_path)
            self.assertEqual(config.vm_name, "kernel-test")
            self.assertEqual(config.vcpus, 4)
            self.assertEqual(config.disk_bus, "virtio")
            self.assertFalse(config.qemu_gdb_debug)

    def test_load_valid_config_with_qemu_gdb_debug_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base_image = root / "base.qcow2"
            kernel_image = root / "bzImage"
            modules = root / "modules.tar.zst"
            for path in (base_image, kernel_image, modules):
                path.write_text("x", encoding="utf-8")

            config_path = root / "config.yaml"
            config_path.write_text(
                f"""
vm_name: kernel-test
base_image_path: {base_image}
vcpus: 4
memory_mb: 8192
bridge_name: br0
qemu_gdb_debug: true
root_ssh_authorized_keys:
  - ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockKey comment
kernel_artifacts:
  kernel_image: {kernel_image}
  kernel_modules_archive: {modules}
kernel_cmdline_append:
  - root=/dev/vda3
""",
                encoding="utf-8",
            )

            config = load_config(config_path)
            self.assertTrue(config.qemu_gdb_debug)

    def test_invalid_ssh_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base_image = root / "base.qcow2"
            kernel_image = root / "bzImage"
            modules = root / "modules.tar.zst"
            for path in (base_image, kernel_image, modules):
                path.write_text("x", encoding="utf-8")

            config_path = root / "config.yaml"
            config_path.write_text(
                f"""
vm_name: kernel-test
base_image_path: {base_image}
vcpus: 4
memory_mb: 8192
bridge_name: br0
root_ssh_authorized_keys:
  - not-a-real-key
kernel_artifacts:
  kernel_image: {kernel_image}
  kernel_modules_archive: {modules}
kernel_cmdline_append:
  - root=/dev/vda3
""",
                encoding="utf-8",
            )

            with self.assertRaises(ValidationError):
                load_config(config_path)

    def test_non_boolean_qemu_gdb_debug_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base_image = root / "base.qcow2"
            kernel_image = root / "bzImage"
            modules = root / "modules.tar.zst"
            for path in (base_image, kernel_image, modules):
                path.write_text("x", encoding="utf-8")

            config_path = root / "config.yaml"
            config_path.write_text(
                f"""
vm_name: kernel-test
base_image_path: {base_image}
vcpus: 4
memory_mb: 8192
bridge_name: br0
qemu_gdb_debug: "yes"
root_ssh_authorized_keys:
  - ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockKey comment
kernel_artifacts:
  kernel_image: {kernel_image}
  kernel_modules_archive: {modules}
kernel_cmdline_append:
  - root=/dev/vda3
""",
                encoding="utf-8",
            )

            with self.assertRaises(ValidationError) as exc:
                load_config(config_path)

            self.assertIn("qemu_gdb_debug must be a boolean", exc.exception.errors)

    def test_kernel_image_requires_root_kernel_arg(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base_image = root / "base.qcow2"
            kernel_image = root / "bzImage"
            modules = root / "modules.tar.zst"
            for path in (base_image, kernel_image, modules):
                path.write_text("x", encoding="utf-8")

            config_path = root / "config.yaml"
            config_path.write_text(
                f"""
vm_name: kernel-test
base_image_path: {base_image}
vcpus: 4
memory_mb: 8192
bridge_name: br0
root_ssh_authorized_keys:
  - ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockKey comment
kernel_artifacts:
  kernel_image: {kernel_image}
  kernel_modules_archive: {modules}
kernel_cmdline_append:
  - console=ttyS0,115200n8
""",
                encoding="utf-8",
            )

            with self.assertRaises(ValidationError) as exc:
                load_config(config_path)

            self.assertIn(
                "kernel_cmdline_append must include a root=... entry when kernel_artifacts.kernel_image is configured",
                exc.exception.errors,
            )
