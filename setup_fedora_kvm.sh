#!/usr/bin/env bash
set -euo pipefail

# -----------------------------
# User-configurable defaults
# -----------------------------
VM_NAME="${VM_NAME:-kernel-fuzz}"
ISO_PATH="${ISO_PATH:-$HOME/iso/Fedora-Workstation-Live-*x86_64.iso}"

# Debug-kernel-friendly sizing (adjust to your host capacity)
VCPUS="${VCPUS:-8}"
RAM_MB="${RAM_MB:-32768}"          # 32GB
DISK_GB="${DISK_GB:-120}"          # 120GB

# Storage location (default libvirt images dir)
DISK_PATH="${DISK_PATH:-/var/lib/libvirt/images/${VM_NAME}.qcow2}"

# Optional: UEFI firmware (Fedora supports both BIOS & UEFI).
# Leave empty to let virt-install choose, or set to "uefi" to force.
FIRMWARE="${FIRMWARE:-}"

# OS variant (used for sane defaults)
OS_VARIANT="${OS_VARIANT:-fedora-unknown}"

# -----------------------------
# Helpers
# -----------------------------
die() { echo "ERROR: $*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

# -----------------------------
# Preflight
# -----------------------------
if [[ "$(id -u)" -ne 0 ]]; then
  die "This script must be run as root."
fi

need_cmd sudo
need_cmd virsh
need_cmd virt-install
need_cmd systemctl
need_cmd qemu-img

echo "sudo dnf install -y @virtualization virt-manager libvirt-daemon-kvm qemu-kvm \
  dnsmasq bridge-utils virt-install
sudo systemctl enable --now libvirtd
"

# Resolve ISO glob (allow wildcard in ISO_PATH)
ISO_RESOLVED=""
if compgen -G "$ISO_PATH" > /dev/null; then
  # pick the newest match if multiple
  ISO_RESOLVED="$(ls -1t $ISO_PATH | head -n1)"
else
  # if no glob match, maybe it's an exact path
  [[ -f "$ISO_PATH" ]] && ISO_RESOLVED="$ISO_PATH"
fi
[[ -n "$ISO_RESOLVED" ]] || die "Fedora ISO not found. Set ISO_PATH to a valid ISO. Current: $ISO_PATH"
[[ -f "$ISO_RESOLVED" ]] || die "ISO does not exist: $ISO_RESOLVED"

echo "Using ISO: $ISO_RESOLVED"
echo "VM name:   $VM_NAME"
echo "vCPUs:     $VCPUS"
echo "RAM (MB):  $RAM_MB"
echo "Disk (GB): $DISK_GB"
echo "Disk path: $DISK_PATH"
echo

# -----------------------------
# Ensure libvirtd is running
# -----------------------------
echo "[1/5] Ensuring libvirtd is enabled and running..."
sudo systemctl enable --now libvirtd

# -----------------------------
# Ensure default NAT network exists and is started
# -----------------------------
echo "[2/5] Ensuring libvirt default NAT network is active..."
if ! sudo virsh net-info default >/dev/null 2>&1; then
  # On most distros this file exists; if not, user likely missing libvirt default networks.
  if [[ -f /usr/share/libvirt/networks/default.xml ]]; then
    sudo virsh net-define /usr/share/libvirt/networks/default.xml
  else
    die "libvirt default network XML not found at /usr/share/libvirt/networks/default.xml.
Install libvirt network defaults (often in libvirt-daemon-config-network) or create a network manually."
  fi
fi

sudo virsh net-start default >/dev/null 2>&1 || true
sudo virsh net-autostart default >/dev/null 2>&1 || true

echo "Default network status:"
sudo virsh net-list --all | sed 's/^/  /'
echo

# -----------------------------
# Create disk if missing
# -----------------------------
echo "[3/5] Creating qcow2 disk if needed..."
if [[ -f "$DISK_PATH" ]]; then
  echo "Disk already exists: $DISK_PATH"
else
  sudo mkdir -p "$(dirname "$DISK_PATH")"
  sudo qemu-img create -f qcow2 "$DISK_PATH" "${DISK_GB}G"
  sudo chown root:root "$DISK_PATH"
  sudo chmod 600 "$DISK_PATH"
  echo "Created disk: $DISK_PATH"
fi
echo

# -----------------------------
# Create VM (if it doesn't already exist)
# -----------------------------
echo "[4/5] Creating VM definition (if needed)..."
if sudo virsh dominfo "$VM_NAME" >/dev/null 2>&1; then
  echo "VM already exists: $VM_NAME"
  echo "To start it: sudo virsh start $VM_NAME"
  echo "To open console: virt-manager or: sudo virsh console $VM_NAME"
  exit 0
fi

# Graphics: SPICE with virtio-gpu (good desktop UX)
# Networking: libvirt default NAT network, virtio model
# Disk: virtio bus
# CPU: host-passthrough for performance
# Channel: spice agent improves clipboard/resolution in some setups
# Console: adds serial console (useful later for automation / kernel debugging)
EXTRA_FIRMWARE_ARGS=()
if [[ "$FIRMWARE" == "uefi" ]]; then
  # virt-install will typically auto-pick UEFI if available; this forces it.
  # Works on most modern distros with OVMF installed.
  EXTRA_FIRMWARE_ARGS+=(--boot uefi)
fi

sudo virt-install \
  --name "$VM_NAME" \
  --memory "$RAM_MB" \
  --vcpus "$VCPUS" \
  --cpu host-passthrough \
  --disk "path=$DISK_PATH,size=$DISK_GB,bus=virtio,cache=none,io=native,format=qcow2" \
  --cdrom "$ISO_RESOLVED" \
  --os-variant "$OS_VARIANT" \
  --network network=default,model=virtio \
  --graphics spice \
  --video virtio \
  --channel spicevmc \
  --console pty,target_type=serial \
  "${EXTRA_FIRMWARE_ARGS[@]}" \
  --noautoconsole

echo
echo "[5/5] Done."
echo "VM created: $VM_NAME"
echo
echo "Next steps:"
echo "  • Open the installer UI: virt-manager"
echo "  • Or start and view console via:"
echo "      sudo virsh start $VM_NAME"
echo "      sudo virt-manager"
echo
echo "After Fedora installs, the VM will boot from disk."
echo "Guest internet should work automatically via libvirt NAT (default network)."

# OUT_IF="$(ip route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')"
echo "Outbound interface: ($OUT_IF) MUST BE SET"

echo 'sudo iptables -I FORWARD 1 -i virbr0 -o "$OUT_IF" -j ACCEPT'
echo 'sudo iptables -I FORWARD 1 -i "$OUT_IF" -o virbr0 -m state --state RELATED,ESTABLISHED -j ACCEPT'


echo "VM setup: "
echo "Might fail due to qemu permissions.  Change /etc/libvirt/qemu.conf user to root -> systemctl daemon-reload, restart libvirtd"
echo "firewall-cmd --zone=libvirt --query-masquerade (--add-masquerade on zone if no)"
echo "Reboot the VM"
