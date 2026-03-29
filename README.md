# kernelvm

`kernelvm` is a Python CLI for disposable Fedora KVM/QEMU guests used in Linux kernel testing. Each deployment gets its own run ID, writable qcow2 overlay, cloud-init inputs, serial console artifacts, and host-side metadata so runs can be restarted or destroyed cleanly.

## Host prerequisites

- Fedora host with `python3`
- `qemu-system-x86_64`
- `qemu-img`
- `cloud-localds`
- `mkfs.ext4` from `e2fsprogs`
- bridge networking already configured on the host, such as `br0`
- SSH public keys for root access
- a Fedora cloud base qcow2 image
- host-built kernel artifacts, at minimum a kernel image and modules archive

## Install

  - make venv to create and install the editable package into .venv
  - make test to run the unit tests through tox

```bash
make venv
```
```bash
make test
```

## CLI commands

```bash
kernelvm validate-config path/to/config.yaml
kernelvm create path/to/config.yaml
kernelvm list-runs
kernelvm status <run-id>
kernelvm ssh-info <run-id>
kernelvm console <run-id>
kernelvm console <run-id> --attach
kernelvm stop <run-id>
kernelvm start <run-id>
kernelvm destroy <run-id>
```

Use `--work-root /path/to/work` to override the default `./work` run storage directory.

## Run directory layout

Each run is stored under `work/<run-id>/`:

```text
work/<run-id>/
  config/
  logs/
  serial/
  cloud-init/
  overlay/
  artifacts/
  metadata.json
```

Important artifacts:

- `config/input-config.yaml`: original config snapshot
- `config/normalized-config.yaml`: normalized config used by the tool
- `logs/kernelvm.log`: CLI activity log
- `logs/qemu.log`: QEMU process log
- `serial/console.log`: captured serial output
- `cloud-init/`: rendered NoCloud inputs and seed image
- `artifacts/payload/`: staged copy-in files, kernel artifacts, manifest, and first-boot scripts
- `artifacts/payload.img`: generated ext4 payload image attached read-only to the guest for large artifact transfer

## Example config

```yaml
vm_name: kernel-test
base_image_path: /var/lib/vm-images/Fedora-Cloud-Base.qcow2
vcpus: 4
memory_mb: 8192
disk_size_gb: 40
bridge_name: br0
hostname: kernel-test-vm

root_ssh_authorized_keys:
  - ssh-ed25519 AAAA... your-key

packages:
  - gcc
  - make
  - git
  - vim

copy_files:
  - src: /path/on/host/configs
    dest: /root/configs

first_boot_commands:
  - systemctl enable sshd

kernel_artifacts:
  kernel_image: /path/to/bzImage
  kernel_modules_archive: /path/to/modules.tar.zst
  system_map: /path/to/System.map
  config: /path/to/config

kernel_cmdline_append:
  - console=ttyS0,115200n8
  - earlycon

serial_log_enabled: true
```

## Workflow

1. Validate the config and host prerequisites with `kernelvm validate-config`.
2. Create a new run with `kernelvm create`, which creates a fresh overlay, stages the payload directory, builds `artifacts/payload.img`, renders cloud-init artifacts, and launches the VM.
3. Inspect the deployment with `kernelvm status` and `kernelvm ssh-info`.
4. Use `kernelvm console --attach` for serial access or inspect `serial/console.log`.
5. Stop and restart an existing run with `kernelvm stop` and `kernelvm start`.
6. Remove the run completely with `kernelvm destroy`.

## Notes

- The tool treats IP discovery as best effort. `ssh-info` and `status` always show hostname and MAC address, and add the guest IP when the host can discover it.
- `create` refuses to start a second run while another run is active.
- The tool does not mutate the base image.
- New runs attach an ext4 payload image for staged files and kernel artifacts; legacy runs that only have the older payload directory metadata still start with the previous compatibility path.
