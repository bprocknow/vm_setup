from __future__ import annotations

import unittest
from pathlib import Path


class ReadmeTests(unittest.TestCase):
    def test_readme_documents_tui_launch_command(self) -> None:
        readme = Path(__file__).resolve().parents[1] / "README.md"

        content = readme.read_text(encoding="utf-8")

        self.assertIn("kernelvm tui path/to/config.yaml", content)
