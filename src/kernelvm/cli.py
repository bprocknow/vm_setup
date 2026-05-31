"""Command-line interface."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import load_config, validate_host_requirements
from .errors import AppError, ValidationError
from .logging_utils import configure_logging
from .network import assess_network_readiness, maybe_detect_ip
from .models import VMConfig
from .provision import render_cloud_init_artifacts
from .qemu import attach_console, create_overlay, start_vm, stop_vm
from .runs import (
    create_run_paths,
    default_work_root,
    destroy_run_root,
    ensure_single_active_run,
    generate_run_id,
    init_metadata,
    list_runs,
    load_metadata,
    persist_run_config,
    refresh_runtime_state,
    save_metadata,
)

LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kernelvm")
    parser.add_argument("--work-root", type=Path, default=default_work_root())
    parser.add_argument("--verbose", action="store_true")

    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("config_file", type=Path)

    validate = subparsers.add_parser("validate-config")
    validate.add_argument("config_file", type=Path)

    for name in ("start", "stop", "status", "ssh-info", "destroy"):
        command = subparsers.add_parser(name)
        command.add_argument("run_id")

    console = subparsers.add_parser("console")
    console.add_argument("run_id")
    console.add_argument("--attach", action="store_true")

    subparsers.add_parser("tui")

    subparsers.add_parser("list-runs")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(verbose=args.verbose)
    work_root = args.work_root.expanduser()

    try:
        if args.command == "validate-config":
            config = load_config(args.config_file.expanduser())
            validate_host_requirements(config)
            print(f"Configuration is valid: {args.config_file}")
            return 0

        if args.command == "create":
            run_id = create_run(args.config_file.expanduser(), work_root, verbose=args.verbose)
            print(run_id)
            return 0

        if args.command == "list-runs":
            for metadata in list_runs(work_root):
                metadata = refresh_runtime_state(metadata)
                maybe_detect_ip(metadata)
                _save_metadata_best_effort(metadata)
                print(
                    "\t".join(
                        [
                            metadata.run_id,
                            metadata.state,
                            metadata.vm_name,
                            metadata.hostname,
                            f"{metadata.vcpus}vcpu/{metadata.memory_mb}MB",
                            metadata.detected_ip or metadata.mac_address,
                            metadata.paths["root"],
                        ]
                    )
            )
            return 0

        if args.command == "tui":
            from .tui import run_tui

            return run_tui(work_root, verbose=args.verbose)

        if args.command == "start":
            _configure_run_logging_if_present(work_root, args.run_id, args.verbose)
            metadata = start_existing_run(args.run_id, work_root)
            print(f"Started run {metadata.run_id}")
            return 0

        if args.command == "stop":
            _configure_run_logging_if_present(work_root, args.run_id, args.verbose)
            metadata = stop_existing_run(args.run_id, work_root)
            print(f"Stopped run {metadata.run_id}")
            return 0

        if args.command == "destroy":
            _configure_run_logging_if_present(work_root, args.run_id, args.verbose)
            destroy_run(args.run_id, work_root)
            print(f"Destroyed run {args.run_id}")
            return 0

        if args.command == "status":
            _configure_run_logging_if_present(work_root, args.run_id, args.verbose)
            print(format_status(load_metadata(work_root, args.run_id)))
            return 0

        if args.command == "ssh-info":
            _configure_run_logging_if_present(work_root, args.run_id, args.verbose)
            print(format_ssh_info(load_metadata(work_root, args.run_id)))
            return 0

        if args.command == "console":
            _configure_run_logging_if_present(work_root, args.run_id, args.verbose)
            metadata = load_metadata(work_root, args.run_id)
            print(attach_console(metadata, attach=args.attach))
            return 0

        parser.error(f"Unknown command: {args.command}")
        return 1
    except ValidationError as exc:
        for line in exc.errors:
            LOGGER.error(line)
        return exc.exit_code
    except AppError as exc:
        LOGGER.error("%s", exc.message)
        return exc.exit_code


def create_run(config_path: Path, work_root: Path, *, verbose: bool = False) -> str:
    config = load_config(config_path)
    validate_host_requirements(config)
    ensure_single_active_run(work_root)
    work_root.mkdir(parents=True, exist_ok=True)

    run_id = generate_run_id()
    paths = create_run_paths(work_root, run_id)
    configure_logging(verbose=verbose, logfile=paths.logs_dir / "kernelvm.log")
    overlay_path = paths.overlay_dir / "overlay.qcow2"
    metadata = init_metadata(
        run_id=run_id,
        config=config,
        config_path=config_path,
        paths=paths,
        overlay_path=overlay_path,
        mac_address=config.static_mac_address or generate_mac_address(),
    )

    persist_run_config(config, config_path, paths)
    save_metadata(metadata)

    try:
        overlay = create_overlay(config, metadata)
        metadata.overlay_path = str(overlay)
        render_cloud_init_artifacts(config, metadata)
        metadata = start_vm(config, metadata)
        metadata = assess_network_readiness(metadata)
        save_metadata(metadata)
    except AppError as exc:
        metadata.state = "failed"
        metadata.errors.append(exc.message)
        save_metadata(metadata)
        raise
    return run_id


def start_existing_run(run_id: str, work_root: Path):
    ensure_single_active_run(work_root, requested_run_id=run_id)
    metadata = refresh_runtime_state(load_metadata(work_root, run_id))
    if metadata.state == "running":
        return metadata
    try:
        config = load_config(Path(metadata.normalized_config_path))
        metadata = start_vm(config, metadata)
        metadata = assess_network_readiness(metadata)
        save_metadata(metadata)
        return metadata
    except ValidationError as exc:
        metadata.state = "failed"
        metadata.errors.extend(exc.errors)
        save_metadata(metadata)
        raise
    except AppError as exc:
        metadata.state = "failed"
        metadata.errors.append(exc.message)
        save_metadata(metadata)
        raise


def stop_existing_run(run_id: str, work_root: Path):
    metadata = refresh_runtime_state(load_metadata(work_root, run_id))
    metadata = stop_vm(metadata)
    save_metadata(metadata)
    return metadata


def destroy_run(run_id: str, work_root: Path) -> None:
    metadata = refresh_runtime_state(load_metadata(work_root, run_id))
    if metadata.state == "running":
        metadata = stop_vm(metadata)
        save_metadata(metadata)
    destroy_run_root(metadata)


def format_status(metadata):
    metadata = refresh_runtime_state(metadata)
    maybe_detect_ip(metadata)
    _save_metadata_best_effort(metadata)
    lines = [
        f"run_id: {metadata.run_id}",
        f"state: {metadata.state}",
        f"vm_name: {metadata.vm_name}",
        f"hostname: {metadata.hostname}",
        f"vcpus: {metadata.vcpus}",
        f"memory_mb: {metadata.memory_mb}",
        f"disk_size_gb: {metadata.disk_size_gb or 'base-image-default'}",
        f"bridge: {metadata.bridge_name}",
        f"mac_address: {metadata.mac_address}",
        f"detected_ip: {metadata.detected_ip or 'unknown'}",
        f"detected_ip_source: {metadata.detected_ip_source or 'unknown'}",
        f"readiness_state: {metadata.readiness_state}",
        f"readiness_reason: {metadata.readiness_reason or 'none'}",
        f"readiness_source: {metadata.readiness_source or 'unknown'}",
        f"overlay: {metadata.overlay_path}",
        f"base_image: {metadata.base_image_path}",
        f"work_dir: {metadata.paths['root']}",
        f"cloud_init_dir: {metadata.paths['cloud_init_dir']}",
        f"artifacts_dir: {metadata.paths['artifacts_dir']}",
        f"payload_image: {metadata.runtime.payload_image or 'legacy-payload-dir'}",
        f"payload_filesystem: {metadata.runtime.payload_filesystem or 'vfat-dir-share'}",
        f"direct_kernel_boot: {'enabled' if metadata.runtime.direct_kernel_boot else 'disabled'}",
        f"direct_kernel_image: {metadata.runtime.direct_kernel_image or 'n/a'}",
        f"direct_kernel_initramfs: {metadata.runtime.direct_kernel_initramfs or 'n/a'}",
        f"direct_kernel_cmdline: {metadata.runtime.direct_kernel_cmdline or 'n/a'}",
        f"logs_dir: {metadata.paths['logs_dir']}",
        f"serial_log: {metadata.runtime.serial_log or 'n/a'}",
    ]
    return "\n".join(lines)


def format_ssh_info(metadata):
    metadata = refresh_runtime_state(metadata)
    maybe_detect_ip(metadata)
    _save_metadata_best_effort(metadata)
    host = metadata.detected_ip or metadata.hostname
    lines = [
        f"run_id: {metadata.run_id}",
        "user: root",
        f"hostname: {metadata.hostname}",
        f"bridge: {metadata.bridge_name}",
        f"mac_address: {metadata.mac_address}",
        f"detected_ip: {metadata.detected_ip or 'unknown'}",
        f"detected_ip_source: {metadata.detected_ip_source or 'unknown'}",
        f"readiness_state: {metadata.readiness_state}",
        f"readiness_reason: {metadata.readiness_reason or 'none'}",
    ]
    if metadata.detected_ip:
        lines.append(f"ssh_command: ssh root@{host}")
    else:
        lines.append("ssh_command: unavailable")
        lines.append(f"inspect_serial_log: {metadata.runtime.serial_log or 'n/a'}")
    return "\n".join(lines)


def _configure_run_logging_if_present(work_root: Path, run_id: str, verbose: bool) -> None:
    metadata_file = work_root / run_id / "metadata.json"
    if metadata_file.exists():
        configure_logging(verbose=verbose, logfile=work_root / run_id / "logs" / "kernelvm.log")


def _save_metadata_best_effort(metadata) -> None:
    try:
        save_metadata(metadata)
    except OSError as exc:
        LOGGER.warning("Could not update metadata for run %s: %s", metadata.run_id, exc)


def generate_mac_address() -> str:
    import secrets

    suffix = [secrets.randbelow(256) for _ in range(3)]
    return "52:54:00:" + ":".join(f"{value:02x}" for value in suffix)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
