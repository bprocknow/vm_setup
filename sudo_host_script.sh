#!/bin/bash

if [ "$(id -u)" -ne 0 ]; then
	echo "This script must be run as root."
	exit 1
fi

run_cmd() {
	"$@"
	rc=$?
	if [ "$rc" -ne 0 ]; then
		echo "Command failed ($rc): $*"
		exit "$rc"
	fi
}

if [ "$(egrep -c '(vmx|svm)' /proc/cpuinfo)" -eq 0 ]; then
	echo "CPU Doesn't support virtualization"
	exit 0
fi

run_cmd dnf install -y @virtualization virt-manager libvirt-daemon-kvm qemu-kvm \
  dnsmasq bridge-utils virt-install
run_cmd systemctl enable --now libvirtd

virsh net-autostart default
virsh net-start default
virsh net-list --all

target_user="${SUDO_USER:-$USER}"
run_cmd usermod -aG libvirt,kvm "$target_user"

run_cmd modprobe kvm
# One of these depending on CPU
if ! modprobe kvm_intel && ! modprobe kvm_amd; then
	echo "Failed to load kvm_intel or kvm_amd."
	exit 1
fi
run_cmd bash -c "lsmod | egrep 'kvm|kvm_intel|kvm_amd'"
