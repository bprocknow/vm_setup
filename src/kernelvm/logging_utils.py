"""Logging helpers."""

from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(verbose: bool = False, logfile: Path | None = None) -> None:
    """Configure console and optional file logging."""

    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    file_logging_warning: str | None = None

    if logfile is not None:
        try:
            logfile.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(logfile, encoding="utf-8"))
        except OSError as exc:
            file_logging_warning = f"File logging disabled for {logfile}: {exc}"

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    if file_logging_warning:
        logging.getLogger(__name__).warning("%s", file_logging_warning)
