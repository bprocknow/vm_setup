"""Firmware resolution helpers."""

from __future__ import annotations

from pathlib import Path

from .errors import AppError

SEABIOS_CANDIDATES = (
    Path("/usr/share/seabios/bios.bin"),
    Path("/usr/share/seabios/bios-256k.bin"),
)


def resolve_legacy_bios_path() -> Path:
    for candidate in SEABIOS_CANDIDATES:
        if candidate.is_file():
            return candidate
    searched = ", ".join(str(candidate) for candidate in SEABIOS_CANDIDATES)
    raise AppError(f"Could not find a SeaBIOS firmware image. Searched: {searched}")
