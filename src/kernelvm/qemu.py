"""QEMU command construction and lifecycle helpers."""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from .errors import AppError
from .models import RunMetadata, VMConfig
from .qmp import qmp_execute
from .utils import ensure_command, run_command

LOGGER = logging.getLogger(__name__)


def create_overlay(config: VMConfig, metadata: RunMetadata) -> Path:
    qemu_img = ensure_command("qemu-img")
    overlay_path = Path(metadata.paths["overlay_dir"]) / "overlay.qcow2"
    run_command(
        [
            qemu_img,
            "create",
            "-f",
            "qcow2",
            "-F",
            "qcow2",
            "-b",
            str(config.base_image_path),
            str(overlay_path),
        ]
    )
    if config.disk_size_gb:
        run_command([qemu_img, "resize", str(overlay_path), f"{config.disk_size_gb}G"])
    return overlay_path


def build_qemu_command(config: VMConfig, metadata: RunMetadata) -> list[str]:
    qemu_system = ensure_command("qemu-system-x86_64")
    qmp_socket = Path(metadata.paths["logs_dir"]) / "qmp.sock"
    pidfile = Path(metadata.paths["logs_dir"]) / "qemu.pid"
    process_log = Path(metadata.paths["logs_dir"]) / "qemu.log"
    serial_socket = Path(metadata.paths["serial_dir"]) / "console.sock"
    serial_log = Path(metadata.paths["serial_dir"]) / "console.log"

    command = [
        qemu_system,
        "-enable-kvm",
        "-name",
        metadata.vm_name,
        "-m",
        str(config.memory_mb),
        "-smp",
        str(config.vcpus),
        "-display",
        "none",
        "-daemonize",
        "-pidfile",
        str(pidfile),
        "-D",
        str(process_log),
        "-qmp",
        f"unix:{qmp_socket},server=on,wait=off",
        "-nic",
        f"bridge,br={config.bridge_name},model={_resolve_net_model(config.net_model)},mac={metadata.mac_address}",
    ]

    command.extend(_disk_args(config.disk_bus, Path(metadata.overlay_path), "osdisk", format_name="qcow2"))

    seed_image = metadata.runtime.seed_image
    if not seed_image:
        raise AppError("Seed image missing from runtime metadata")
    command.extend(["-drive", f"file={seed_image},if=virtio,media=disk,format=raw,readonly=on"])

    payload_dir = metadata.runtime.payload_dir
    if payload_dir:
        command.extend(["-drive", f"file=fat:ro:{payload_dir},if=virtio,media=disk,format=raw"])

    if config.serial_log_enabled:
        command.extend(
            [
                "-chardev",
                f"socket,id=charserial,path={serial_socket},server=on,wait=off,logfile={serial_log},logappend=on",
                "-serial",
                "chardev:charserial",
            ]
        )

    return command


def start_vm(config: VMConfig, metadata: RunMetadata) -> RunMetadata:
    command = build_qemu_command(config, metadata)
    run_command(command)

    pidfile = Path(metadata.paths["logs_dir"]) / "qemu.pid"
    if not pidfile.exists():
        raise AppError("QEMU did not create a pidfile")

    metadata.runtime.pid = int(pidfile.read_text(encoding="utf-8").strip())
    metadata.runtime.pidfile = str(pidfile)
    metadata.runtime.qmp_socket = str(Path(metadata.paths["logs_dir"]) / "qmp.sock")
    metadata.runtime.serial_socket = str(Path(metadata.paths["serial_dir"]) / "console.sock")
    metadata.runtime.serial_log = str(Path(metadata.paths["serial_dir"]) / "console.log")
    metadata.runtime.process_log = str(Path(metadata.paths["logs_dir"]) / "qemu.log")
    metadata.state = "running"
    return metadata


def stop_vm(metadata: RunMetadata, *, timeout_seconds: int = 30) -> RunMetadata:
    pid = metadata.runtime.pid
    if pid is None:
        metadata.state = "stopped"
        return metadata

    if metadata.runtime.qmp_socket:
        try:
            qmp_execute(Path(metadata.runtime.qmp_socket), "system_powerdown")
        except Exception as exc:
            LOGGER.warning("Graceful shutdown via QMP failed: %s", exc)

    deadline = time.time() + timeout_seconds
    while time.time() < deadline and _is_running(pid):
        time.sleep(1)

    if _is_running(pid):
        os.kill(pid, signal.SIGTERM)
        time.sleep(2)

    if _is_running(pid):
        os.kill(pid, signal.SIGKILL)

    metadata.runtime.pid = None
    metadata.state = "stopped"
    return metadata


def attach_console(metadata: RunMetadata, *, attach: bool = False) -> str:
    serial_socket = metadata.runtime.serial_socket
    serial_log = metadata.runtime.serial_log
    if not serial_socket:
        raise AppError("Serial console is not configured for this run")
    if not attach:
        return f"Serial socket: {serial_socket}\nSerial log: {serial_log}"

    socat = shutil.which("socat")
    if not socat:
        return f"socat is not installed.\nSerial socket: {serial_socket}\nSerial log: {serial_log}"

    subprocess.run([socat, "-", f"UNIX-CONNECT:{serial_socket}"], check=False)
    return f"Attached to {serial_socket}"


def _disk_args(bus: str, path: Path, drive_id: str, *, format_name: str) -> list[str]:
    if bus == "virtio":
        return ["-drive", f"file={path},if=virtio,format={format_name},discard=unmap"]
    if bus == "scsi":
        return [
            "-device",
            "virtio-scsi-pci,id=scsi0",
            "-drive",
            f"file={path},if=none,id={drive_id},format={format_name}",
            "-device",
            f"scsi-hd,drive={drive_id}",
        ]
    if bus == "sata":
        return [
            "-device",
            "ich9-ahci,id=ahci0",
            "-drive",
            f"file={path},if=none,id={drive_id},format={format_name}",
            "-device",
            f"ide-hd,drive={drive_id},bus=ahci0.0",
        ]
    raise AppError(f"Unsupported disk bus: {bus}")


def _resolve_net_model(model: str) -> str:
    if model == "virtio":
        return "virtio-net-pci"
    return model


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
