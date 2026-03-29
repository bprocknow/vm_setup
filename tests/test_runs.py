from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kernelvm.models import KernelArtifacts, VMConfig
from kernelvm.runs import create_run_paths, generate_run_id, init_metadata, load_metadata, save_metadata


class RunMetadataTests(unittest.TestCase):
    def _config(self, root: Path) -> VMConfig:
        kernel = KernelArtifacts(
            kernel_image=root / "bzImage",
            kernel_modules_archive=root / "modules.tar.zst",
        )
        for path in (kernel.kernel_image, kernel.kernel_modules_archive):
            path.write_text("x", encoding="utf-8")
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
        )

    def test_run_id_is_unique_shape(self) -> None:
        run_id = generate_run_id()
        self.assertRegex(run_id, r"^\d{14}-[0-9a-f]{6}$")

    def test_metadata_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "base.qcow2").write_text("x", encoding="utf-8")
            config = self._config(root)
            paths = create_run_paths(root / "work", "run-1")
            metadata = init_metadata(
                run_id="run-1",
                config=config,
                config_path=root / "config.yaml",
                paths=paths,
                overlay_path=paths.overlay_dir / "overlay.qcow2",
                mac_address="52:54:00:12:34:56",
            )
            save_metadata(metadata)

            loaded = load_metadata(root / "work", "run-1")
            self.assertEqual(loaded.run_id, "run-1")
            self.assertEqual(loaded.mac_address, "52:54:00:12:34:56")
