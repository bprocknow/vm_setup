"""Best-effort guest network discovery."""

from __future__ import annotations

import ipaddress
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .host_network import HOST_BRIDGE_SOURCE, diagnose_bridge
from .utils import run_command

SERIAL_LOG_SOURCE = "serial-log"
HOST_NEIGH_SOURCE = "host-ip-neigh"
READINESS_CHECK_SOURCE = "readiness-check"
READINESS_READY = "ready"
READINESS_UNREADY = "networking-unready"
READINESS_UNKNOWN = "unknown"
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


@dataclass(slots=True)
class NetworkObservation:
    detected_ip: str | None = None
    detected_ip_source: str | None = None
    readiness_reason: str | None = None
    readiness_source: str | None = None
    ssh_ready: bool = False


def detect_ip_for_mac(mac_address: str) -> tuple[str | None, str | None]:
    try:
        result = run_command(["ip", "-json", "neigh"], capture_output=True)
    except Exception:
        return None, None

    try:
        entries = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None, None

    target_mac = mac_address.lower()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("lladdr", "")).lower() == target_mac:
            destination = entry.get("dst")
            if isinstance(destination, str) and _is_usable_ipv4(destination):
                return destination, HOST_NEIGH_SOURCE
    return None, None


def maybe_detect_ip(metadata) -> str | None:
    observe_network(metadata)
    return metadata.detected_ip


def assess_network_readiness(metadata, *, timeout_seconds: int = 90, poll_interval_seconds: int = 2):
    observation = observe_network(metadata)
    if metadata.readiness_state in {READINESS_READY, READINESS_UNREADY}:
        return metadata

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        time.sleep(poll_interval_seconds)
        observation = observe_network(metadata)
        if metadata.readiness_state in {READINESS_READY, READINESS_UNREADY}:
            return metadata

    metadata.readiness_state = READINESS_UNREADY
    metadata.readiness_reason = observation.readiness_reason or "No usable guest IPv4 address was observed before the readiness timeout."
    metadata.readiness_source = observation.readiness_source or READINESS_CHECK_SOURCE
    return metadata


def observe_network(metadata) -> NetworkObservation:
    allow_saved_ip = metadata.runtime.serial_log_offset is None
    detected_ip = metadata.detected_ip if allow_saved_ip else None
    detected_ip_source = metadata.detected_ip_source if allow_saved_ip else None

    serial_observation = (
        inspect_serial_log(
            Path(metadata.runtime.serial_log),
            start_offset=metadata.runtime.serial_log_offset or 0,
        )
        if metadata.runtime.serial_log
        else NetworkObservation()
    )
    bridge_diagnosis = diagnose_bridge(metadata.bridge_name)

    if serial_observation.detected_ip:
        detected_ip = serial_observation.detected_ip
        detected_ip_source = serial_observation.detected_ip_source
    elif not bridge_diagnosis and not serial_observation.readiness_reason and not detected_ip:
        detected_ip, detected_ip_source = detect_ip_for_mac(metadata.mac_address)

    if detected_ip:
        metadata.detected_ip = detected_ip
        metadata.detected_ip_source = detected_ip_source
        metadata.readiness_state = READINESS_READY
        metadata.readiness_reason = None
        metadata.readiness_source = detected_ip_source
    elif bridge_diagnosis:
        metadata.detected_ip = None
        metadata.detected_ip_source = None
        metadata.readiness_state = READINESS_UNREADY
        metadata.readiness_reason = bridge_diagnosis
        metadata.readiness_source = HOST_BRIDGE_SOURCE
    elif serial_observation.readiness_reason:
        metadata.detected_ip = None
        metadata.detected_ip_source = None
        metadata.readiness_state = READINESS_UNREADY
        metadata.readiness_reason = serial_observation.readiness_reason
        metadata.readiness_source = serial_observation.readiness_source
    elif metadata.readiness_state == READINESS_UNKNOWN:
        metadata.detected_ip = None
        metadata.detected_ip_source = None
        metadata.readiness_state = READINESS_UNKNOWN
        metadata.readiness_reason = None
        metadata.readiness_source = None

    return NetworkObservation(
        detected_ip=metadata.detected_ip,
        detected_ip_source=metadata.detected_ip_source,
        readiness_reason=metadata.readiness_reason,
        readiness_source=metadata.readiness_source,
        ssh_ready=serial_observation.ssh_ready,
    )


def inspect_serial_log(path: Path, *, start_offset: int = 0) -> NetworkObservation:
    if not path.exists():
        return NetworkObservation()

    try:
        with path.open("rb") as handle:
            file_size = path.stat().st_size
            if start_offset < 0 or start_offset > file_size:
                start_offset = 0
            handle.seek(start_offset)
            text = handle.read().decode("utf-8", errors="ignore")
    except OSError:
        return NetworkObservation()

    if not text:
        return NetworkObservation()

    detected_ip = _extract_ipv4_from_serial(text)
    if detected_ip:
        return NetworkObservation(detected_ip=detected_ip, detected_ip_source=SERIAL_LOG_SOURCE, ssh_ready=_serial_has_sshd_ready(text))

    readiness_reason = _classify_serial_failure(text)
    return NetworkObservation(
        readiness_reason=readiness_reason,
        readiness_source=SERIAL_LOG_SOURCE if readiness_reason else None,
        ssh_ready=_serial_has_sshd_ready(text),
    )


def _extract_ipv4_from_serial(text: str) -> str | None:
    candidates = IPV4_RE.findall(text)
    for candidate in candidates:
        if _is_usable_ipv4(candidate):
            return candidate
    return None


def _classify_serial_failure(text: str) -> str | None:
    if "Could not resolve host" in text or "Could not resolve hostname" in text:
        return "Guest DNS resolution failed during cloud-init package installation."
    if "Failed to start NetworkManager-wait-online.service" in text:
        return "NetworkManager-wait-online timed out before the guest obtained usable networking."
    if "link-local" in text or "fe80::" in text:
        return "Guest interface reached only link-local IPv6; no usable IPv4 address was observed."
    if "cloud-final.service" in text and "FAILED" in text:
        return "cloud-final.service failed before guest provisioning completed."
    if "Started sshd.service" in text:
        return "sshd started, but no usable guest IPv4 address was observed."
    return None


def _serial_has_sshd_ready(text: str) -> bool:
    return "Started sshd.service" in text or "Started OpenSSH server daemon" in text


def _is_usable_ipv4(value: str) -> bool:
    if "/" in value:
        value = value.split("/", 1)[0]
    if not IPV4_RE.fullmatch(value):
        return False
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return False
    if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
        return False
    if str(address).startswith("255."):
        return False
    return True
