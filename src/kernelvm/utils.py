"""Utility helpers."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .errors import AppError, CommandError

LOGGER = logging.getLogger(__name__)


def ensure_command(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise AppError(f"Required command not found on PATH: {name}")
    return path


def run_command(
    args: list[str],
    *,
    cwd: Path | None = None,
    capture_output: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    LOGGER.debug("Running command: %s", " ".join(args))
    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=capture_output,
        check=False,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        stdout = result.stdout.strip() if result.stdout else ""
        detail = stderr or stdout or f"command exited with {result.returncode}"
        raise CommandError(f"Command failed: {' '.join(args)}\n{detail}", exit_code=result.returncode or 1)
    return result


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def require_within(root: Path, target: Path) -> None:
    root_resolved = root.resolve()
    target_resolved = target.resolve()
    if not target_resolved.is_relative_to(root_resolved):
        raise AppError(f"Refusing to operate outside run root: {target_resolved}")
