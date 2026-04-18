"""Host-side bridge inspection helpers."""

from __future__ import annotations

from pathlib import Path

HOST_BRIDGE_SOURCE = "host-bridge-check"


def diagnose_bridge(bridge_name: str, *, sys_class_net: Path = Path("/sys/class/net")) -> str | None:
    bridge_path = sys_class_net / bridge_name
    if not bridge_path.exists() or not (bridge_path / "bridge").exists():
        return None

    member_names = _bridge_members(bridge_path / "brif")
    operstate = _read_optional_text(bridge_path / "operstate")
    carrier = _read_optional_text(bridge_path / "carrier")

    if operstate == "down" and not member_names:
        return (
            f"Host bridge {bridge_name} is down and has no attached interfaces. "
            "Move the host uplink and IP configuration onto the bridge before launching the VM."
        )
    if operstate == "down" and member_names:
        members = ", ".join(member_names)
        return (
            f"Host bridge {bridge_name} is down even though it has attached interfaces ({members}). "
            "Bring the bridge up and confirm the uplink is active before launching the VM."
        )
    if not member_names and carrier == "0" and operstate in {"dormant", "lowerlayerdown"}:
        return (
            f"Host bridge {bridge_name} has no active carrier or attached interfaces. "
            "Connect the uplink to the bridge before launching the VM."
        )
    return None


def _bridge_members(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        return sorted(entry.name for entry in path.iterdir())
    except OSError:
        return []


def _read_optional_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
