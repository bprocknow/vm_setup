"""Normalized config and metadata models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SUPPORTED_DISK_BUS = {"virtio", "scsi", "sata"}
SUPPORTED_NET_MODELS = {"virtio", "virtio-net-pci", "e1000", "rtl8139"}
SUPPORTED_SELINUX_MODES = {"enforcing", "permissive", "disabled"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class CopyFileSpec:
    src: Path
    dest: str

    def to_dict(self) -> dict[str, Any]:
        return {"src": str(self.src), "dest": self.dest}


@dataclass(slots=True)
class KernelArtifacts:
    kernel_image: Path
    kernel_modules_archive: Path
    system_map: Path | None = None
    config: Path | None = None
    initramfs: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kernel_image": str(self.kernel_image),
            "kernel_modules_archive": str(self.kernel_modules_archive),
            "system_map": str(self.system_map) if self.system_map else None,
            "config": str(self.config) if self.config else None,
            "initramfs": str(self.initramfs) if self.initramfs else None,
        }


@dataclass(slots=True)
class VMConfig:
    base_image_path: Path
    vm_name: str | None
    vm_name_prefix: str | None
    vcpus: int
    memory_mb: int
    disk_size_gb: int | None
    bridge_name: str
    kernel_artifacts: KernelArtifacts
    root_ssh_authorized_keys: list[str]
    packages: list[str] = field(default_factory=list)
    copy_files: list[CopyFileSpec] = field(default_factory=list)
    first_boot_commands: list[str] = field(default_factory=list)
    kernel_cmdline_append: list[str] = field(default_factory=list)
    disk_bus: str = "virtio"
    net_model: str = "virtio"
    serial_log_enabled: bool = True
    preserve_overlay_on_stop: bool = True
    static_mac_address: str | None = None
    hostname: str | None = None
    selinux_mode: str | None = None
    cloud_init_user_data_overrides: dict[str, Any] = field(default_factory=dict)

    def resolved_vm_name(self, run_id: str | None = None) -> str:
        if self.vm_name:
            return self.vm_name
        prefix = self.vm_name_prefix or "kernelvm"
        if run_id:
            return f"{prefix}-{run_id}"
        return prefix

    def resolved_hostname(self, run_id: str | None = None) -> str:
        return self.hostname or self.resolved_vm_name(run_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_image_path": str(self.base_image_path),
            "vm_name": self.vm_name,
            "vm_name_prefix": self.vm_name_prefix,
            "vcpus": self.vcpus,
            "memory_mb": self.memory_mb,
            "disk_size_gb": self.disk_size_gb,
            "bridge_name": self.bridge_name,
            "kernel_artifacts": self.kernel_artifacts.to_dict(),
            "root_ssh_authorized_keys": list(self.root_ssh_authorized_keys),
            "packages": list(self.packages),
            "copy_files": [item.to_dict() for item in self.copy_files],
            "first_boot_commands": list(self.first_boot_commands),
            "kernel_cmdline_append": list(self.kernel_cmdline_append),
            "disk_bus": self.disk_bus,
            "net_model": self.net_model,
            "serial_log_enabled": self.serial_log_enabled,
            "preserve_overlay_on_stop": self.preserve_overlay_on_stop,
            "static_mac_address": self.static_mac_address,
            "hostname": self.hostname,
            "selinux_mode": self.selinux_mode,
            "cloud_init_user_data_overrides": self.cloud_init_user_data_overrides,
        }


@dataclass(slots=True)
class RunPaths:
    root: Path
    config_dir: Path
    logs_dir: Path
    serial_dir: Path
    cloud_init_dir: Path
    overlay_dir: Path
    artifacts_dir: Path

    def to_dict(self) -> dict[str, str]:
        return {key: str(value) for key, value in asdict(self).items()}


@dataclass(slots=True)
class RuntimeInfo:
    pid: int | None = None
    qmp_socket: str | None = None
    serial_socket: str | None = None
    serial_log: str | None = None
    serial_log_offset: int | None = None
    process_log: str | None = None
    pidfile: str | None = None
    seed_image: str | None = None
    payload_dir: str | None = None
    payload_image: str | None = None
    payload_filesystem: str | None = None
    payload_label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RunMetadata:
    run_id: str
    vm_name: str
    hostname: str
    state: str
    created_at: str
    updated_at: str
    config_path: str
    normalized_config_path: str
    base_image_path: str
    overlay_path: str
    bridge_name: str
    mac_address: str
    detected_ip: str | None
    disk_bus: str
    net_model: str
    vcpus: int
    memory_mb: int
    disk_size_gb: int | None
    paths: dict[str, str]
    kernel_artifacts: dict[str, Any]
    detected_ip_source: str | None = None
    readiness_state: str = "unknown"
    readiness_reason: str | None = None
    readiness_source: str | None = None
    runtime: RuntimeInfo = field(default_factory=RuntimeInfo)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["runtime"] = self.runtime.to_dict()
        return data

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RunMetadata":
        runtime = RuntimeInfo(**raw.get("runtime", {}))
        data = dict(raw)
        data["runtime"] = runtime
        return cls(**data)
