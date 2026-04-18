from __future__ import annotations

import logging
import unittest
from pathlib import Path
from unittest import mock

from kernelvm.logging_utils import configure_logging


class LoggingUtilsTests(unittest.TestCase):
    def test_configure_logging_ignores_unwritable_logfile(self) -> None:
        with mock.patch("kernelvm.logging_utils.logging.FileHandler", side_effect=PermissionError("denied")):
            configure_logging(logfile=Path("/tmp/forbidden.log"))

        self.assertGreaterEqual(len(logging.getLogger().handlers), 1)
