from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kernelvm.errors import AppError
from kernelvm.firmware import resolve_legacy_bios_path


class FirmwareResolutionTests(unittest.TestCase):
    def test_resolve_legacy_bios_path_returns_first_existing_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bios_path = Path(tmpdir) / "bios.bin"
            bios_path.write_text("bios", encoding="utf-8")

            with mock.patch("kernelvm.firmware.SEABIOS_CANDIDATES", (bios_path,)):
                self.assertEqual(resolve_legacy_bios_path(), bios_path)

    def test_resolve_legacy_bios_path_raises_when_missing(self) -> None:
        with mock.patch("kernelvm.firmware.SEABIOS_CANDIDATES", (Path("/missing/bios.bin"),)):
            with self.assertRaises(AppError):
                resolve_legacy_bios_path()
