# vm_setup

sudo dnf install -y @virtualization virt-manager libvirt-daemon-kvm qemu-kvm \
  dnsmasq bridge-utils virt-install
sudo systemctl enable --now libvirtd

sudo virsh net-autostart default
sudo virsh net-start default || true
sudo virsh net-list --all

sudo usermod -aG libvirt,kvm "$USER"
newgrp kvm

