"""Cloud-init and payload generation."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from textwrap import dedent
from typing import Any

import yaml

from .errors import AppError
from .models import RunMetadata, VMConfig
from .utils import ensure_command, run_command, write_json

LOGGER = logging.getLogger(__name__)
PAYLOAD_LABEL = "KERNELVMPAYLOAD"
PAYLOAD_FILESYSTEM = "ext4"
PAYLOAD_IMAGE_NAME = "payload.img"
PAYLOAD_IMAGE_ALIGN_BYTES = 4 * 1024 * 1024
PAYLOAD_IMAGE_MIN_BYTES = 128 * 1024 * 1024
PAYLOAD_IMAGE_BASE_OVERHEAD_BYTES = 64 * 1024 * 1024
PRIMARY_NETWORK_ID = "primary"


def render_cloud_init_artifacts(config: VMConfig, metadata: RunMetadata) -> None:
    cloud_init_dir = Path(metadata.paths["cloud_init_dir"])
    payload_dir = Path(metadata.paths["artifacts_dir"]) / "payload"
    payload_dir.mkdir(parents=True, exist_ok=True)

    stage_payload(config, metadata, payload_dir)
    payload_image_path = build_payload_image(payload_dir, metadata)

    user_data = build_user_data(config, metadata)
    meta_data = {
        "instance-id": metadata.run_id,
        "local-hostname": metadata.hostname,
    }

    network_config = build_network_config(metadata)

    user_data_path = cloud_init_dir / "user-data"
    meta_data_path = cloud_init_dir / "meta-data"
    network_config_path = cloud_init_dir / "network-config"
    seed_image_path = cloud_init_dir / "seed.img"

    user_data_path.write_text("#cloud-config\n" + yaml.safe_dump(user_data, sort_keys=False), encoding="utf-8")
    meta_data_path.write_text(yaml.safe_dump(meta_data, sort_keys=False), encoding="utf-8")
    network_config_path.write_text(yaml.safe_dump(network_config, sort_keys=False), encoding="utf-8")

    cloud_localds = ensure_command("cloud-localds")
    run_command(
        [
            cloud_localds,
            "-N",
            str(network_config_path),
            str(seed_image_path),
            str(user_data_path),
            str(meta_data_path),
        ]
    )

    metadata.runtime.seed_image = str(seed_image_path)
    metadata.runtime.payload_dir = str(payload_dir)
    metadata.runtime.payload_image = str(payload_image_path)
    metadata.runtime.payload_filesystem = PAYLOAD_FILESYSTEM
    metadata.runtime.payload_label = PAYLOAD_LABEL


def build_network_config(metadata: RunMetadata) -> dict[str, Any]:
    return {
        "version": 2,
        "ethernets": {
            PRIMARY_NETWORK_ID: {
                "match": {
                    "macaddress": metadata.mac_address.lower(),
                },
                "dhcp4": True,
                "dhcp6": False,
            }
        },
    }


def stage_payload(config: VMConfig, metadata: RunMetadata, payload_dir: Path) -> None:
    copied_root = payload_dir / "copy_files"
    kernel_root = payload_dir / "kernel"
    scripts_root = payload_dir / "scripts"
    copied_root.mkdir(parents=True, exist_ok=True)
    kernel_root.mkdir(parents=True, exist_ok=True)
    scripts_root.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {"copy_files": [], "kernel_artifacts": {}}

    for index, copy_spec in enumerate(config.copy_files):
        entry_dir = copied_root / f"{index:02d}"
        entry_dir.mkdir(parents=True, exist_ok=True)
        target = entry_dir / copy_spec.src.name
        if copy_spec.src.is_dir():
            shutil.copytree(copy_spec.src, target)
            source_type = "dir"
        else:
            shutil.copy2(copy_spec.src, target)
            source_type = "file"
        manifest["copy_files"].append(
            {
                "index": index,
                "src_name": copy_spec.src.name,
                "payload_path": str(target.relative_to(payload_dir)),
                "dest": copy_spec.dest,
                "source_type": source_type,
            }
        )

    for key, path in _payload_kernel_artifacts(config).items():
        if path is None:
            continue
        source = Path(path)
        target = kernel_root / source.name
        shutil.copy2(source, target)
        manifest["kernel_artifacts"][key] = str(target.relative_to(payload_dir))

    (scripts_root / "provision-firstboot.sh").write_text(build_firstboot_script(config), encoding="utf-8")
    write_json(payload_dir / "manifest.json", manifest)


def build_payload_image(payload_dir: Path, metadata: RunMetadata) -> Path:
    mkfs_ext4 = ensure_command("mkfs.ext4")
    payload_image_path = Path(metadata.paths["artifacts_dir"]) / PAYLOAD_IMAGE_NAME
    payload_image_size = calculate_payload_image_size(payload_dir)

    payload_image_path.parent.mkdir(parents=True, exist_ok=True)
    with payload_image_path.open("wb") as handle:
        handle.truncate(payload_image_size)

    try:
        run_command(
            [
                mkfs_ext4,
                "-q",
                "-d",
                str(payload_dir),
                "-L",
                PAYLOAD_LABEL,
                "-F",
                str(payload_image_path),
            ]
        )
    except AppError:
        payload_image_path.unlink(missing_ok=True)
        raise

    return payload_image_path


def calculate_payload_image_size(payload_dir: Path) -> int:
    total_size = 0
    inode_count = 0

    for current_root, dirnames, filenames in os.walk(payload_dir):
        inode_count += 1 + len(dirnames)
        root_path = Path(current_root)
        for filename in filenames:
            inode_count += 1
            total_size += (root_path / filename).stat().st_size

    overhead = PAYLOAD_IMAGE_BASE_OVERHEAD_BYTES + max(16 * 1024 * 1024, total_size // 10) + (inode_count * 4096)
    required_size = max(PAYLOAD_IMAGE_MIN_BYTES, total_size + overhead)
    return _align_up(required_size, PAYLOAD_IMAGE_ALIGN_BYTES)


def build_user_data(config: VMConfig, metadata: RunMetadata) -> dict[str, Any]:
    write_files = [
        {
            "path": "/usr/local/sbin/kernelvm-discover-payload.sh",
            "permissions": "0755",
            "owner": "root:root",
            "content": build_payload_locator_script(metadata.run_id),
        }
    ]

    user_data: dict[str, Any] = {
        "disable_root": False,
        "ssh_pwauth": False,
        "packages": config.packages,
        "users": [
            {
                "name": "root",
                "lock_passwd": True,
                "shell": "/bin/bash",
                "ssh_authorized_keys": config.root_ssh_authorized_keys,
            }
        ],
        "write_files": write_files,
        "runcmd": [
            ["/usr/local/sbin/kernelvm-discover-payload.sh", metadata.run_id],
        ],
    }

    if config.selinux_mode:
        user_data["bootcmd"] = [f"setenforce {'0' if config.selinux_mode != 'enforcing' else '1'} || true"]

    return _deep_merge(user_data, config.cloud_init_user_data_overrides)


def build_payload_locator_script(run_id: str) -> str:
    return dedent(
        f"""\
        #!/bin/bash
        set -euo pipefail

        RUN_ID="${{1:-{run_id}}}"
        PAYLOAD_MOUNT="/mnt/kernelvm-payload"
        STATUS_DIR="/var/lib/kernelvm"
        mkdir -p "$STATUS_DIR" "$PAYLOAD_MOUNT"

        DEVICE=""
        SELECTED_FSTYPE=""
        for candidate in /dev/vd[b-z] /dev/sd[b-z] /dev/hd[b-z]; do
          if [[ -b "$candidate" ]]; then
            label="$(blkid -o value -s LABEL "$candidate" 2>/dev/null || true)"
            fstype="$(blkid -o value -s TYPE "$candidate" 2>/dev/null || true)"
            if [[ "$label" == "{PAYLOAD_LABEL}" || "$fstype" == "vfat" ]]; then
              if mount -o ro "$candidate" "$PAYLOAD_MOUNT" 2>/dev/null; then
                if [[ -f "$PAYLOAD_MOUNT/scripts/provision-firstboot.sh" ]]; then
                  DEVICE="$candidate"
                  SELECTED_FSTYPE="$fstype"
                  break
                fi
                umount "$PAYLOAD_MOUNT" || true
              fi
            fi
          fi
        done

        if [[ -z "$DEVICE" ]]; then
          echo "kernelvm: payload device not found" > "$STATUS_DIR/${{RUN_ID}}.status"
          exit 1
        fi

        echo "kernelvm: using payload device $DEVICE (${{SELECTED_FSTYPE:-unknown}})" >> "$STATUS_DIR/${{RUN_ID}}.log"
        bash "$PAYLOAD_MOUNT/scripts/provision-firstboot.sh" "$RUN_ID" "$PAYLOAD_MOUNT"
        umount "$PAYLOAD_MOUNT"
        """
    )


def build_firstboot_script(config: VMConfig) -> str:
    copy_commands: list[str] = []
    for index, copy_spec in enumerate(config.copy_files):
        payload_item = f"$PAYLOAD_ROOT/copy_files/{index:02d}/{Path(copy_spec.src).name}"
        copy_commands.append(f'mkdir -p "$(dirname "{copy_spec.dest}")"')
        if Path(copy_spec.src).is_dir():
            copy_commands.append(f'mkdir -p "{copy_spec.dest}"')
            copy_commands.append(f'cp -a "{payload_item}/." "{copy_spec.dest}/"')
        else:
            copy_commands.append(f'cp -a "{payload_item}" "{copy_spec.dest}"')

    modules_archive_name = Path(config.kernel_artifacts.kernel_modules_archive).name
    system_map_name = Path(config.kernel_artifacts.system_map).name if config.kernel_artifacts.system_map else None
    config_name = Path(config.kernel_artifacts.config).name if config.kernel_artifacts.config else None

    first_boot_commands = "\n".join(config.first_boot_commands) if config.first_boot_commands else "true"

    optional_artifact_steps = []
    if system_map_name:
        optional_artifact_steps.append(
            f"""if [[ -f "$KERNEL_ROOT/{system_map_name}" ]]; then
  cp -a "$KERNEL_ROOT/{system_map_name}" /boot/ || true
fi"""
        )
    if config_name:
        optional_artifact_steps.append(
            f"""if [[ -f "$KERNEL_ROOT/{config_name}" ]]; then
  cp -a "$KERNEL_ROOT/{config_name}" /boot/config-kernelvm || true
fi"""
        )
    return dedent(
        f"""\
        #!/bin/bash
        set -euo pipefail
        RUN_ID="${{1:?run-id-required}}"
        PAYLOAD_ROOT="${{2:?payload-root-required}}"
        STATUS_DIR="/var/lib/kernelvm"
        LOG_FILE="$STATUS_DIR/${{RUN_ID}}.log"
        STATUS_FILE="$STATUS_DIR/${{RUN_ID}}.status"
        mkdir -p "$STATUS_DIR"
        exec > >(tee -a "$LOG_FILE") 2>&1

        trap 'echo "failed" > "$STATUS_FILE"' ERR

        {"\n".join(copy_commands) if copy_commands else "true"}

        KERNEL_ROOT="$PAYLOAD_ROOT/kernel"
        if [[ -f "$KERNEL_ROOT/{modules_archive_name}" ]]; then
          mkdir -p /lib/modules
          tar --auto-compress -xf "$KERNEL_ROOT/{modules_archive_name}" -C /
          RUNNING_RELEASE="$(uname -r)"
          if [[ ! -d "/lib/modules/$RUNNING_RELEASE" && ! -d "/usr/lib/modules/$RUNNING_RELEASE" ]]; then
            echo "kernelvm: modules archive did not install modules for running kernel $RUNNING_RELEASE"
            FOUND_MODULE_DIRS="$(find /lib/modules /usr/lib/modules -mindepth 1 -maxdepth 1 -type d -printf '%P ' 2>/dev/null || true)"
            echo "kernelvm: found module directories after extraction: ${{FOUND_MODULE_DIRS:-none}}"
            exit 1
          fi
        fi
        {"\n".join(optional_artifact_steps) if optional_artifact_steps else "true"}

        {first_boot_commands}
        echo "success" > "$STATUS_FILE"
        """
    )


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _align_up(value: int, alignment: int) -> int:
    remainder = value % alignment
    if remainder == 0:
        return value
    return value + alignment - remainder


def _payload_kernel_artifacts(config: VMConfig) -> dict[str, str | None]:
    return {
        "kernel_modules_archive": str(config.kernel_artifacts.kernel_modules_archive),
        "system_map": str(config.kernel_artifacts.system_map) if config.kernel_artifacts.system_map else None,
        "config": str(config.kernel_artifacts.config) if config.kernel_artifacts.config else None,
    }
