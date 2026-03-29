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
""",
                encoding="utf-8",
            )

            config = load_config(config_path)
            self.assertEqual(config.vm_name, "kernel-test")
            self.assertEqual(config.vcpus, 4)
            self.assertEqual(config.disk_bus, "virtio")

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
""",
                encoding="utf-8",
            )

            with self.assertRaises(ValidationError):
                load_config(config_path)
