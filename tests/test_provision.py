from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kernelvm.models import KernelArtifacts, RunMetadata, RuntimeInfo, VMConfig
from kernelvm.provision import build_firstboot_script, render_cloud_init_artifacts


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
            kernel_cmdline_append=["console=ttyS0,115200n8", "earlycon"],
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
            self.assertIn("grubby --set-default", script)
            self.assertIn("console=ttyS0,115200n8 earlycon", script)
            self.assertNotIn("None", script)

