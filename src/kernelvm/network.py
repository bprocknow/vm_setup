"""Best-effort guest network discovery."""

from __future__ import annotations

import json
from pathlib import Path

from .utils import run_command


def detect_ip_for_mac(mac_address: str) -> str | None:
    try:
        result = run_command(["ip", "-json", "neigh"], capture_output=True)
    except Exception:
        return None

    try:
        entries = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None

    target_mac = mac_address.lower()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("lladdr", "")).lower() == target_mac:
            destination = entry.get("dst")
            if isinstance(destination, str) and destination:
                return destination
    return None


def maybe_detect_ip(metadata) -> str | None:
    if metadata.detected_ip:
        return metadata.detected_ip
    detected = detect_ip_for_mac(metadata.mac_address)
    if detected:
        metadata.detected_ip = detected
    return metadata.detected_ip
