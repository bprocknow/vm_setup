"""Config loading and validation."""

from __future__ import annotations

import re
import os
from pathlib import Path
from typing import Any

import yaml

from .errors import AppError, ValidationError
from .firmware import resolve_legacy_bios_path
from .host_network import diagnose_bridge
from .models import (
    CopyFileSpec,
    KernelArtifacts,
    SUPPORTED_DISK_BUS,
    SUPPORTED_NET_MODELS,
    SUPPORTED_SELINUX_MODES,
    VMConfig,
)
from .utils import ensure_command

SSH_KEY_RE = re.compile(r"^(ssh-(rsa|ed25519)|ecdsa-sha2-nistp(256|384|521)) [A-Za-z0-9+/=]+(?: .*)?$")
MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def load_config(path: Path) -> VMConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValidationError(["Configuration file must contain a YAML mapping at the top level"])
    return validate_config(raw, config_path=path)


def validate_config(raw: dict[str, Any], *, config_path: Path | None = None) -> VMConfig:
    errors: list[str] = []

    def require_str(name: str) -> str | None:
        value = raw.get(name)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{name} is required and must be a non-empty string")
            return None
        return value.strip()

    def require_int(name: str) -> int | None:
        value = raw.get(name)
        if not isinstance(value, int) or value <= 0:
            errors.append(f"{name} is required and must be a positive integer")
            return None
        return value

    base_image_path_raw = require_str("base_image_path")
    bridge_name = require_str("bridge_name")
    vcpus = require_int("vcpus")
    memory_mb = require_int("memory_mb")

    vm_name = raw.get("vm_name")
    vm_name_prefix = raw.get("vm_name_prefix")
    if not isinstance(vm_name, str) and not isinstance(vm_name_prefix, str):
        errors.append("Either vm_name or vm_name_prefix must be provided")
    if isinstance(vm_name, str) and not vm_name.strip():
        errors.append("vm_name must not be empty")
    if isinstance(vm_name_prefix, str) and not vm_name_prefix.strip():
        errors.append("vm_name_prefix must not be empty")

    disk_size_gb = raw.get("disk_size_gb")
    if disk_size_gb is not None and (not isinstance(disk_size_gb, int) or disk_size_gb <= 0):
        errors.append("disk_size_gb must be a positive integer when provided")

    disk_bus = raw.get("disk_bus", "virtio")
    if disk_bus not in SUPPORTED_DISK_BUS:
        errors.append(f"disk_bus must be one of: {', '.join(sorted(SUPPORTED_DISK_BUS))}")

    net_model = raw.get("net_model", "virtio")
    if net_model not in SUPPORTED_NET_MODELS:
        errors.append(f"net_model must be one of: {', '.join(sorted(SUPPORTED_NET_MODELS))}")

    qemu_gdb_debug = raw.get("qemu_gdb_debug", False)
    if not isinstance(qemu_gdb_debug, bool):
        errors.append("qemu_gdb_debug must be a boolean")

    serial_log_enabled = raw.get("serial_log_enabled", True)
    if not isinstance(serial_log_enabled, bool):
        errors.append("serial_log_enabled must be a boolean")

    preserve_overlay_on_stop = raw.get("preserve_overlay_on_stop", True)
    if not isinstance(preserve_overlay_on_stop, bool):
        errors.append("preserve_overlay_on_stop must be a boolean")

    hostname = raw.get("hostname")
    if hostname is not None and (not isinstance(hostname, str) or not hostname.strip()):
        errors.append("hostname must be a non-empty string when provided")

    static_mac_address = raw.get("static_mac_address")
    if static_mac_address is not None:
        if not isinstance(static_mac_address, str) or not MAC_RE.fullmatch(static_mac_address.lower()):
            errors.append("static_mac_address must be a MAC address like 52:54:00:12:34:56")
        else:
            static_mac_address = static_mac_address.lower()

    selinux_mode = raw.get("selinux_mode")
    if selinux_mode is not None and selinux_mode not in SUPPORTED_SELINUX_MODES:
        errors.append(f"selinux_mode must be one of: {', '.join(sorted(SUPPORTED_SELINUX_MODES))}")

    packages = _list_of_strings(raw.get("packages", []), "packages", errors)
    first_boot_commands = _list_of_strings(raw.get("first_boot_commands", []), "first_boot_commands", errors)
    kernel_cmdline_append = _list_of_strings(raw.get("kernel_cmdline_append", []), "kernel_cmdline_append", errors)

    root_keys = _list_of_strings(raw.get("root_ssh_authorized_keys"), "root_ssh_authorized_keys", errors, required=True)
    for index, key in enumerate(root_keys):
        if not SSH_KEY_RE.fullmatch(key):
            errors.append(f"root_ssh_authorized_keys[{index}] is not a supported SSH public key")

    copy_specs: list[CopyFileSpec] = []
    copy_files_raw = raw.get("copy_files", [])
    if copy_files_raw is None:
        copy_files_raw = []
    if not isinstance(copy_files_raw, list):
        errors.append("copy_files must be a list when provided")
    else:
        for index, item in enumerate(copy_files_raw):
            if not isinstance(item, dict):
                errors.append(f"copy_files[{index}] must be a mapping")
                continue
            src = item.get("src")
            dest = item.get("dest")
            if not isinstance(src, str) or not src.strip():
                errors.append(f"copy_files[{index}].src must be a non-empty string")
                continue
            if not isinstance(dest, str) or not dest.strip():
                errors.append(f"copy_files[{index}].dest must be a non-empty string")
                continue
            copy_specs.append(CopyFileSpec(src=Path(src).expanduser(), dest=dest))

    kernel_raw = raw.get("kernel_artifacts")
    kernel_artifacts = _parse_kernel_artifacts(kernel_raw, errors)

    overrides = raw.get("cloud_init_user_data_overrides", {})
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict):
        errors.append("cloud_init_user_data_overrides must be a mapping when provided")

    if errors:
        raise ValidationError(errors)

    return VMConfig(
        base_image_path=Path(base_image_path_raw).expanduser(),
        vm_name=vm_name.strip() if isinstance(vm_name, str) else None,
        vm_name_prefix=vm_name_prefix.strip() if isinstance(vm_name_prefix, str) else None,
        vcpus=vcpus or 1,
        memory_mb=memory_mb or 1024,
        disk_size_gb=disk_size_gb,
        bridge_name=bridge_name or "",
        kernel_artifacts=kernel_artifacts,
        root_ssh_authorized_keys=root_keys,
        packages=packages,
        copy_files=copy_specs,
        first_boot_commands=first_boot_commands,
        kernel_cmdline_append=kernel_cmdline_append,
        disk_bus=disk_bus,
        net_model=net_model,
        qemu_gdb_debug=qemu_gdb_debug,
        serial_log_enabled=serial_log_enabled,
        preserve_overlay_on_stop=preserve_overlay_on_stop,
        static_mac_address=static_mac_address,
        hostname=hostname.strip() if isinstance(hostname, str) else None,
        selinux_mode=selinux_mode,
        cloud_init_user_data_overrides=overrides,
    )


def validate_host_requirements(config: VMConfig) -> None:
    errors: list[str] = []

    for command_name in ("mkfs.ext4",):
        try:
            ensure_command(command_name)
        except AppError as exc:
            errors.append(exc.message)

    try:
        resolve_legacy_bios_path()
    except AppError as exc:
        errors.append(exc.message)

    if not config.base_image_path.exists():
        errors.append(f"base_image_path does not exist: {config.base_image_path}")
    elif not config.base_image_path.is_file():
        errors.append(f"base_image_path is not a file: {config.base_image_path}")
    elif not os.access(config.base_image_path, os.R_OK):
        errors.append(f"base_image_path is not readable: {config.base_image_path}")

    bridge_path = Path("/sys/class/net") / config.bridge_name
    if not bridge_path.exists():
        errors.append(f"bridge_name does not exist on host: {config.bridge_name}")
    elif not (bridge_path / "bridge").exists():
        errors.append(f"bridge_name is not a bridge device: {config.bridge_name}")
    else:
        bridge_diagnosis = diagnose_bridge(config.bridge_name)
        if bridge_diagnosis:
            errors.append(bridge_diagnosis)

    for label, path in config.kernel_artifacts.to_dict().items():
        if path is None:
            continue
        path_obj = Path(path)
        if not path_obj.exists():
            errors.append(f"kernel_artifacts.{label} does not exist: {path_obj}")
        elif not path_obj.is_file():
            errors.append(f"kernel_artifacts.{label} is not a file: {path_obj}")

    for copy_spec in config.copy_files:
        if not copy_spec.src.exists():
            errors.append(f"copy_files source does not exist: {copy_spec.src}")

    if errors:
        raise ValidationError(errors)


def _list_of_strings(value: Any, name: str, errors: list[str], *, required: bool = False) -> list[str]:
    if value is None:
        value = []
    if required and value == []:
        errors.append(f"{name} is required and must be a non-empty list of strings")
        return []
    if not isinstance(value, list):
        errors.append(f"{name} must be a list of strings")
        return []
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{name}[{index}] must be a non-empty string")
            continue
        items.append(item.strip())
    return items


def _parse_kernel_artifacts(value: Any, errors: list[str]) -> KernelArtifacts:
    if not isinstance(value, dict):
        errors.append("kernel_artifacts is required and must be a mapping")
        return KernelArtifacts(Path("."), Path("."))

    def artifact(name: str, *, required: bool) -> Path | None:
        raw_value = value.get(name)
        if raw_value is None and not required:
            return None
        if not isinstance(raw_value, str) or not raw_value.strip():
            errors.append(f"kernel_artifacts.{name} must be a non-empty string")
            return None
        return Path(raw_value).expanduser()

    return KernelArtifacts(
        kernel_image=artifact("kernel_image", required=True) or Path("."),
        kernel_modules_archive=artifact("kernel_modules_archive", required=True) or Path("."),
        system_map=artifact("system_map", required=False),
        config=artifact("config", required=False),
        initramfs=artifact("initramfs", required=False),
    )
