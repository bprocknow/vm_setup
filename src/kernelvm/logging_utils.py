"""Logging helpers."""

from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(verbose: bool = False, logfile: Path | None = None) -> None:
    """Configure console and optional file logging."""

    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = [logging.StreamHandler()]

    if logfile is not None:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(logfile, encoding="utf-8"))

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
