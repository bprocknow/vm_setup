from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from kernelvm.errors import AppError
from kernelvm.models import KernelArtifacts, RunMetadata, RuntimeInfo, VMConfig
from kernelvm.provision import (
    PAYLOAD_LABEL,
    PRIMARY_NETWORK_ID,
    build_firstboot_script,
    build_network_config,
    build_payload_image,
    build_payload_locator_script,
    calculate_payload_image_size,
    render_cloud_init_artifacts,
    stage_payload,
)


class ProvisioningTests(unittest.TestCase):
    def _config(self, root: Path) -> VMConfig:
        kernel = KernelArtifacts(
            kernel_image=root / "bzImage",
            kernel_modules_archive=root / "modules.tar.zst",
            system_map=root / "System.map",
            config=root / "config",
        )
        for path in (kernel.kernel_image, kernel.kernel_modules_archive, kernel.system_map, kernel.config):
            path.write_text("x", encoding="utf-8")
        src_dir = root / "configs"
        src_dir.mkdir()
        (src_dir / "inner.txt").write_text("payload", encoding="utf-8")
        return VMConfig(
            base_image_path=root / "base.qcow2",
            vm_name="kernel-test",
            vm_name_prefix=None,
            vcpus=2,
            memory_mb=2048,
            disk_size_gb=20,
            bridge_name="br0",
            kernel_artifacts=kernel,
            root_ssh_authorized_keys=["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockKey comment"],
            copy_files=[],
            first_boot_commands=["echo ready"],
            kernel_cmdline_append=["root=/dev/vda3", "console=ttyS0,115200n8", "earlycon"],
        )

    def _metadata(self, root: Path) -> RunMetadata:
        run_root = root / "work" / "run-1"
        for subdir in ("config", "logs", "serial", "cloud-init", "overlay", "artifacts"):
            (run_root / subdir).mkdir(parents=True, exist_ok=True)
        return RunMetadata(
            run_id="run-1",
            vm_name="kernel-test",
            hostname="kernel-test",
            state="created",
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
            runtime=RuntimeInfo(),
            errors=[],
        )

    def test_firstboot_script_contains_kernel_and_cmdline_logic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "base.qcow2").write_text("x", encoding="utf-8")
            config = self._config(root)
            script = build_firstboot_script(config)
            self.assertNotIn("grubby --set-default", script)
            self.assertNotIn("console=ttyS0,115200n8 earlycon", script)
            self.assertIn('KERNEL_ROOT="$PAYLOAD_ROOT/kernel"', script)
            self.assertNotIn('cp -a "$KERNEL_ROOT/bzImage" /boot/', script)
            self.assertNotIn("/var/lib/kernelvm/kernel-inputs", script)
            self.assertNotIn("None", script)

    def test_payload_locator_script_supports_ext4_and_legacy_vfat(self) -> None:
        script = build_payload_locator_script("run-1")
        self.assertIn(PAYLOAD_LABEL, script)
        self.assertIn('"$fstype" == "vfat"', script)
        self.assertIn('scripts/provision-firstboot.sh', script)

    def test_render_cloud_init_artifacts_records_payload_image_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "base.qcow2").write_text("x", encoding="utf-8")
            config = self._config(root)
            metadata = self._metadata(root)

            with mock.patch("kernelvm.provision.ensure_command", side_effect=["mkfs.ext4", "cloud-localds"]), mock.patch(
                "kernelvm.provision.run_command"
            ) as run_command:
                render_cloud_init_artifacts(config, metadata)

            self.assertTrue(metadata.runtime.payload_dir.endswith("/artifacts/payload"))
            self.assertTrue(metadata.runtime.payload_image.endswith("/artifacts/payload.img"))
            self.assertEqual(metadata.runtime.payload_filesystem, "ext4")
            self.assertEqual(metadata.runtime.payload_label, PAYLOAD_LABEL)
            self.assertEqual(run_command.call_args_list[0].args[0][0], "mkfs.ext4")
            self.assertEqual(run_command.call_args_list[1].args[0][0], "cloud-localds")
            network_config = yaml.safe_load(
                (root / "work" / "run-1" / "cloud-init" / "network-config").read_text(encoding="utf-8")
            )
            self.assertIn(PRIMARY_NETWORK_ID, network_config["ethernets"])
            self.assertEqual(
                network_config["ethernets"][PRIMARY_NETWORK_ID]["match"]["macaddress"],
                "52:54:00:12:34:56",
            )
            self.assertNotIn("eth0", network_config["ethernets"])

    def test_build_network_config_matches_guest_mac(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            metadata = self._metadata(root)
            network_config = build_network_config(metadata)

            self.assertEqual(network_config["ethernets"][PRIMARY_NETWORK_ID]["match"]["macaddress"], "52:54:00:12:34:56")
            self.assertTrue(network_config["ethernets"][PRIMARY_NETWORK_ID]["dhcp4"])
            self.assertFalse(network_config["ethernets"][PRIMARY_NETWORK_ID]["dhcp6"])

    def test_stage_payload_copies_kernel_artifacts_from_absolute_host_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifact_root = root / "outside-artifacts"
            artifact_root.mkdir()
            (root / "base.qcow2").write_text("x", encoding="utf-8")
            kernel_image = artifact_root / "bzImage"
            modules_archive = artifact_root / "modules.tar.zst"
            system_map = artifact_root / "System.map"
            config_path = artifact_root / "config"
            for path, content in (
                (kernel_image, "kernel-image"),
                (modules_archive, "modules-archive"),
                (system_map, "system-map"),
                (config_path, "kernel-config"),
            ):
                path.write_text(content, encoding="utf-8")

            config = VMConfig(
                base_image_path=root / "base.qcow2",
                vm_name="kernel-test",
                vm_name_prefix=None,
                vcpus=2,
                memory_mb=2048,
                disk_size_gb=20,
                bridge_name="br0",
                kernel_artifacts=KernelArtifacts(
                    kernel_image=kernel_image,
                    kernel_modules_archive=modules_archive,
                    system_map=system_map,
                    config=config_path,
                ),
                root_ssh_authorized_keys=["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockKey comment"],
                kernel_cmdline_append=["root=/dev/vda3"],
            )
            metadata = self._metadata(root)
            payload_dir = root / "payload"

            stage_payload(config, metadata, payload_dir)

            self.assertEqual((payload_dir / "kernel" / "modules.tar.zst").read_text(encoding="utf-8"), "modules-archive")
            self.assertEqual((payload_dir / "kernel" / "System.map").read_text(encoding="utf-8"), "system-map")
            self.assertEqual((payload_dir / "kernel" / "config").read_text(encoding="utf-8"), "kernel-config")
            self.assertFalse((payload_dir / "kernel" / "bzImage").exists())
            manifest = yaml.safe_load((payload_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn("kernel_image", manifest["kernel_artifacts"])

    def test_build_payload_image_sizes_for_large_modules_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            payload_dir = root / "payload"
            kernel_dir = payload_dir / "kernel"
            kernel_dir.mkdir(parents=True)
            large_archive = kernel_dir / "modules.tar.zst"
            with large_archive.open("wb") as handle:
                handle.truncate(513 * 1024 * 1024)

            metadata = self._metadata(root)
            image_size = calculate_payload_image_size(payload_dir)
            self.assertGreater(image_size, large_archive.stat().st_size)

            with mock.patch("kernelvm.provision.ensure_command", return_value="mkfs.ext4"), mock.patch(
                "kernelvm.provision.run_command"
            ) as run_command:
                image_path = build_payload_image(payload_dir, metadata)

            self.assertEqual(image_path.name, "payload.img")
            self.assertEqual(image_path.stat().st_size, image_size)
            self.assertIn("mkfs.ext4", run_command.call_args.args[0][0])

    def test_build_payload_image_removes_partial_file_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            payload_dir = root / "payload"
            payload_dir.mkdir()
            (payload_dir / "manifest.json").write_text("{}", encoding="utf-8")
            metadata = self._metadata(root)

            with mock.patch("kernelvm.provision.ensure_command", return_value="mkfs.ext4"), mock.patch(
                "kernelvm.provision.run_command", side_effect=AppError("mkfs failed")
            ):
                with self.assertRaises(AppError):
                    build_payload_image(payload_dir, metadata)

            self.assertFalse((root / "work" / "run-1" / "artifacts" / "payload.img").exists())
