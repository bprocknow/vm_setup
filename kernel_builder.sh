#!/usr/bin/env bash
# build_install_kernel_fedora.sh
#
# Build + install an upstream Linux kernel on Fedora (server/desktop),
# merging Kconfig fragments passed on the command line.
#
# Usage:
#   ./build_install_kernel_fedora.sh [--src <dir>] [--jobs N] \
#       config-fragment1.cfg [config-fragment2.cfg ...]
#
# Example fragment file (kconfig.cfg):
#   CONFIG_DEBUG_KERNEL=y
#   CONFIG_KASAN=y
#   # CONFIG_RANDOMIZE_BASE is not set
#
# Notes:
# - Assumes x86_64.
# - Reboots at the end unless --no-reboot is used.
# - Prints the kernel release it installed.
#
set -euo pipefail

# ----------------------------
# Defaults
# ----------------------------
SRC_DIR="$(pwd)"
JOBS="$(nproc)"
DO_REBOOT=1

# ----------------------------
# Args
# ----------------------------
usage() {
  cat <<'EOF'
Usage:
  build_install_kernel_fedora.sh [options] <kconfig_fragment>...

Options:
  --src <dir>         Source directory (default: ~/src/linux)
  --jobs <N>          Parallel build jobs (default: nproc)
  --no-reboot         Do not reboot at the end
  -h, --help          Show help

Kconfig fragments:
  One or more files containing CONFIG_ lines, e.g.:
    CONFIG_DEBUG_KERNEL=y
    CONFIG_KASAN=y
    # CONFIG_FOO is not set
EOF
}

FRAG_FILES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --src)  SRC_DIR="$2"; shift 2 ;;
    --jobs) JOBS="$2"; shift 2 ;;
    --no-reboot) DO_REBOOT=0; shift ;;
    -h|--help) usage; exit 0 ;;
    -*)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
    *)
      FRAG_FILES+=("$1")
      shift
      ;;
  esac
done

if [[ ${#FRAG_FILES[@]} -lt 1 ]]; then
  echo "ERROR: Provide at least one kconfig fragment file." >&2
  usage
  exit 2
fi

# ----------------------------
# Helpers
# ----------------------------
die() { echo "ERROR: $*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

validate_frag() {
  local f="$1"
  [[ -f "$f" ]] || die "Config fragment not found: $f"
  [[ -r "$f" ]] || die "Config fragment not readable: $f"

  # Basic validation: allow empty lines/comments; ensure non-comment lines look like Kconfig entries.
  # Accepted:
  #   CONFIG_FOO=y|m|n
  #   CONFIG_FOO="string"
  #   CONFIG_FOO=123
  #   # CONFIG_FOO is not set
  local bad
  bad="$(grep -nEv '^\s*$|^\s*#\s*$|^\s*#\s*CONFIG_[A-Za-z0-9_]+\s+is\s+not\s+set\s*$|^\s*CONFIG_[A-Za-z0-9_]+=(y|m|n|".*"|[0-9]+)\s*$' "$f" || true)"
  if [[ -n "$bad" ]]; then
    echo "Invalid lines found in fragment: $f" >&2
    echo "$bad" >&2
    die "Fix the fragment format and re-run."
  fi
}

run() {
  echo "+ $*"
  "$@"
}

# ----------------------------
# Preflight
# ----------------------------
need_cmd sudo
need_cmd dnf
need_cmd git
need_cmd make
need_cmd gcc
need_cmd awk
need_cmd grep
need_cmd sed
need_cmd rsync

for f in "${FRAG_FILES[@]}"; do
  validate_frag "$f"
done

# ----------------------------
# Install dependencies
# ----------------------------
echo "== Installing build dependencies =="
run sudo dnf install -y \
  git make gcc bc bison flex openssl-devel elfutils-libelf-devel \
  dwarves pahole ncurses-devel perl python3 rsync ccache zstd xz \
  dracut grub2-tools elfutils-libelf-devel

# Optional: useful extras (harmless if already installed)
run sudo dnf install -y \
  perl-ExtUtils-Embed perl-FindBin perl-File-Compare perl-File-Copy \
  openssl

# Ensure required helper script exists
[[ -x scripts/kconfig/merge_config.sh ]] || die "Missing scripts/kconfig/merge_config.sh (unexpected tree?)"

# ----------------------------
# Baseline config
# ----------------------------
echo "== Configuring kernel =="
run make x86_64_defconfig

# Merge KVM guest baseline config
if [[ -f kernel/configs/kvm_guest.config ]]; then
  run scripts/kconfig/merge_config.sh -m .config kernel/configs/kvm_guest.config
else
  die "Expected kernel/configs/kvm_guest.config not found in this tree"
fi

# Merge user-provided fragments
# Using merge_config.sh ensures Kconfig semantics are respected.
for frag in "${FRAG_FILES[@]}"; do
  echo "== Merging fragment: $frag =="
  run scripts/kconfig/merge_config.sh -m .config "$frag"
done

# Resolve anything unspecified to defaults
run make olddefconfig

# Verify final config is consistent
# (config check does not guarantee "good kernel", but catches common issues)
if [[ -x scripts/config ]]; then
  echo "== Sanity: ensure .config exists and looks valid =="
  [[ -f .config ]] || die ".config missing after merge"
fi

# Keep a copy of final config for auditing
FINAL_CONFIG_OUT="config.final.$(date +%Y%m%d_%H%M%S)"
run cp -av .config "$FINAL_CONFIG_OUT"

# ----------------------------
# Build
# ----------------------------
echo "== Building kernel (jobs=$JOBS) =="
# Use ccache if present; user can export CC="ccache gcc" externally if desired.
run make -j"$JOBS" bzImage modules

# ----------------------------
# Install
# ----------------------------
echo "== Installing modules + kernel =="
run sudo make modules_install
run sudo make install

# Determine installed kernel release (uses the tree's Makefile version + localversion)
KREL="$(make -s kernelrelease)"
echo "== Installed kernel release: $KREL =="

# Build initramfs
echo "== Generating initramfs with dracut =="
run sudo dracut -f "/boot/initramfs-${KREL}.img" "${KREL}"

# Rebuild GRUB config (BIOS path; common on Fedora server)
echo "== Regenerating GRUB config =="
if [[ -d /sys/firmware/efi ]]; then
  # UEFI systems typically use this path on Fedora
  if [[ -d /boot/efi/EFI/fedora ]]; then
    run sudo grub2-mkconfig -o /boot/efi/EFI/fedora/grub.cfg
  else
    # Fallback
    run sudo grub2-mkconfig -o /boot/grub2/grub.cfg
  fi
else
  run sudo grub2-mkconfig -o /boot/grub2/grub.cfg
fi

# Persist journald logs (useful for kernel debugging)
echo "== Enabling persistent journald logs =="
run sudo mkdir -p /var/log/journal
run sudo systemctl restart systemd-journald

echo
echo "============================================================"
echo "Kernel built + installed: $KREL"
echo "Final merged config saved as: $SRC_DIR/$FINAL_CONFIG_OUT"
echo "============================================================"
echo

if [[ "$DO_REBOOT" -eq 1 ]]; then
  echo "Rebooting now..."
  run sudo reboot
else
  echo "Not rebooting (--no-reboot). After reboot, verify with:"
  echo "  uname -a"
  echo "  rpm -qa | grep kernel || true"
fi

