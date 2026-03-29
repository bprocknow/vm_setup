from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kernelvm.models import RunMetadata, RuntimeInfo
from kernelvm.runs import destroy_run_root


class DestroySafetyTests(unittest.TestCase):
    def test_destroy_run_root_removes_only_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_root = root / "work" / "run-1"
            other_root = root / "work" / "run-2"
            run_root.mkdir(parents=True)
            other_root.mkdir(parents=True)
            (run_root / "metadata.json").write_text("{}", encoding="utf-8")
            (other_root / "metadata.json").write_text("{}", encoding="utf-8")
            (run_root / "artifacts").mkdir()
            (run_root / "artifacts" / "payload.img").write_text("payload", encoding="utf-8")
            (run_root / "artifacts" / "payload").mkdir()
            (run_root / "artifacts" / "payload" / "manifest.json").write_text("{}", encoding="utf-8")

            metadata = RunMetadata(
                run_id="run-1",
                vm_name="kernel-test",
                hostname="kernel-test",
                state="stopped",
                created_at="now",
                updated_at="now",
                config_path=str(run_root / "config.yaml"),
                normalized_config_path=str(run_root / "config" / "normalized-config.yaml"),
                base_image_path="/tmp/base.qcow2",
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

            destroy_run_root(metadata)

            self.assertFalse(run_root.exists())
            self.assertTrue(other_root.exists())
