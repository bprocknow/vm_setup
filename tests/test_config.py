from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kernelvm.config import _detect_kernel_release, _extract_release_from_archive_entry, _validate_kernel_artifact_compatibility, load_config
from kernelvm.errors import ValidationError
from kernelvm.models import KernelArtifacts, VMConfig


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

    def test_extract_release_from_archive_entry_supports_lib_modules_layout(self) -> None:
        self.assertEqual(
            _extract_release_from_archive_entry("lib/modules/6.19.0/kernel/drivers/virtio/virtio_blk.ko"),
            "6.19.0",
        )

    def test_extract_release_from_archive_entry_supports_usr_lib_modules_layout(self) -> None:
        self.assertEqual(
            _extract_release_from_archive_entry("usr/lib/modules/6.19.0/modules.dep"),
            "6.19.0",
        )

    def test_validate_kernel_artifact_compatibility_reports_release_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
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
                kernel_cmdline_append=["root=/dev/vda3"],
            )

            with mock.patch("kernelvm.config._detect_kernel_release", return_value="6.19.0"), mock.patch(
                "kernelvm.config._detect_modules_archive_release", return_value="7.0.0-rc5-00385-gbe762d8b6dd7"
            ):
                errors = _validate_kernel_artifact_compatibility(config)

            self.assertEqual(len(errors), 1)
            self.assertIn("archive installs modules for 7.0.0-rc5-00385-gbe762d8b6dd7", errors[0])
            self.assertIn("kernel_image reports 6.19.0", errors[0])

    def test_detect_kernel_release_follows_symlinked_kernel_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "bzImage-real"
            link = root / "bzImage"
            target.write_text("kernel", encoding="utf-8")
            link.symlink_to(target)

            with mock.patch("kernelvm.config.run_command") as run_command:
                run_command.return_value.stdout = f"{target}: Linux kernel x86 boot executable, bzImage, version 6.19.0 (builder@host) #1 SMP"
                release = _detect_kernel_release(link)

            self.assertEqual(release, "6.19.0")
            self.assertEqual(run_command.call_args.args[0], ["file", str(target.resolve())])
