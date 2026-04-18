"""Run directory and metadata management."""

from __future__ import annotations

import logging
import os
import secrets
import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import yaml

from .errors import AppError
from .models import RunMetadata, RunPaths, RuntimeInfo, VMConfig, utc_now
from .utils import read_json, require_within, write_json

LOGGER = logging.getLogger(__name__)
RUN_DIRS = ("config", "logs", "serial", "cloud-init", "overlay", "artifacts")


def default_work_root() -> Path:
    return Path.cwd() / "work"


def generate_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"{stamp}-{secrets.token_hex(3)}"


def create_run_paths(work_root: Path, run_id: str) -> RunPaths:
    root = work_root / run_id
    root.mkdir(parents=True, exist_ok=False)
    mapping = {
        "config_dir": root / "config",
        "logs_dir": root / "logs",
        "serial_dir": root / "serial",
        "cloud_init_dir": root / "cloud-init",
        "overlay_dir": root / "overlay",
        "artifacts_dir": root / "artifacts",
    }
    for path in mapping.values():
        path.mkdir(parents=True, exist_ok=True)
    return RunPaths(root=root, **mapping)


def metadata_path_for(work_root: Path, run_id: str) -> Path:
    return work_root / run_id / "metadata.json"


def normalized_config_path(paths: RunPaths) -> Path:
    return paths.config_dir / "normalized-config.yaml"


def raw_config_snapshot_path(paths: RunPaths) -> Path:
    return paths.config_dir / "input-config.yaml"


def init_metadata(
    *,
    run_id: str,
    config: VMConfig,
    config_path: Path,
    paths: RunPaths,
    overlay_path: Path,
    mac_address: str,
) -> RunMetadata:
    now = utc_now()
    return RunMetadata(
        run_id=run_id,
        vm_name=config.resolved_vm_name(run_id),
        hostname=config.resolved_hostname(run_id),
        state="created",
        created_at=now,
        updated_at=now,
        config_path=str(config_path),
        normalized_config_path=str(normalized_config_path(paths)),
        base_image_path=str(config.base_image_path),
        overlay_path=str(overlay_path),
        bridge_name=config.bridge_name,
        mac_address=mac_address,
        detected_ip=None,
        disk_bus=config.disk_bus,
        net_model=config.net_model,
        vcpus=config.vcpus,
        memory_mb=config.memory_mb,
        disk_size_gb=config.disk_size_gb,
        paths=paths.to_dict(),
        kernel_artifacts=config.kernel_artifacts.to_dict(),
        detected_ip_source=None,
        readiness_state="unknown",
        readiness_reason=None,
        readiness_source=None,
        runtime=RuntimeInfo(),
        errors=[],
    )


def persist_run_config(config: VMConfig, source_path: Path, paths: RunPaths) -> None:
    raw_config_snapshot_path(paths).write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    normalized_config_path(paths).write_text(
        yaml.safe_dump(config.to_dict(), sort_keys=False),
        encoding="utf-8",
    )


def save_metadata(metadata: RunMetadata) -> None:
    metadata.updated_at = utc_now()
    write_json(Path(metadata.paths["root"]) / "metadata.json", metadata.to_dict())


def load_metadata(work_root: Path, run_id: str) -> RunMetadata:
    path = metadata_path_for(work_root, run_id)
    if not path.exists():
        raise AppError(f"Run not found: {run_id}", exit_code=3)
    return RunMetadata.from_dict(read_json(path))


def list_runs(work_root: Path) -> list[RunMetadata]:
    if not work_root.exists():
        return []
    results: list[RunMetadata] = []
    for metadata_path in sorted(work_root.glob("*/metadata.json")):
        try:
            results.append(RunMetadata.from_dict(read_json(metadata_path)))
        except Exception as exc:  # pragma: no cover - corrupted files remain visible in logs
            LOGGER.warning("Skipping unreadable metadata file %s: %s", metadata_path, exc)
    return results


def process_is_running(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def refresh_runtime_state(metadata: RunMetadata) -> RunMetadata:
    if metadata.state == "running" and not process_is_running(metadata.runtime.pid):
        metadata.state = "stopped"
        metadata.runtime.pid = None
    return metadata


def reset_network_observation(metadata: RunMetadata) -> RunMetadata:
    metadata.detected_ip = None
    metadata.detected_ip_source = None
    metadata.readiness_state = "unknown"
    metadata.readiness_reason = None
    metadata.readiness_source = None
    return metadata


def ensure_single_active_run(work_root: Path, requested_run_id: str | None = None) -> None:
    for metadata in list_runs(work_root):
        metadata = refresh_runtime_state(metadata)
        if metadata.state == "running" and process_is_running(metadata.runtime.pid):
            if requested_run_id is None or metadata.run_id != requested_run_id:
                raise AppError(
                    f"Another run is active: {metadata.run_id}. Stop it before starting a different run.",
                    exit_code=4,
                )


def destroy_run_root(metadata: RunMetadata) -> None:
    root = Path(metadata.paths["root"])
    require_within(root, root)
    shutil.rmtree(root)


def update_metadata_state(metadata: RunMetadata, state: str, *, error: str | None = None) -> RunMetadata:
    errors = list(metadata.errors)
    if error:
        errors.append(error)
    return replace(metadata, state=state, errors=errors)
