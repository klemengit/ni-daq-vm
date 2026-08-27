#!/usr/bin/env bash
# Step 1: install the virtualisation stack on Arch/Omarchy and start libvirt.
# Idempotent — safe to re-run.
set -euo pipefail

PKGS=(qemu-desktop libvirt virt-install virt-manager dnsmasq edk2-ovmf libisoburn openbsd-netcat)

echo "==> Installing packages"
sudo pacman -S --needed --noconfirm "${PKGS[@]}"

echo "==> Enabling libvirt"
if systemctl list-unit-files libvirtd.socket &>/dev/null; then
    sudo systemctl enable --now libvirtd.socket
else
    sudo systemctl enable --now virtqemud.socket virtnetworkd.socket virtstoraged.socket
fi

echo "==> Ensuring the default NAT network exists and autostarts"
if ! sudo virsh net-info default &>/dev/null; then
    sudo virsh net-define /usr/share/libvirt/networks/default.xml
fi
sudo virsh net-autostart default
sudo virsh net-start default 2>/dev/null || echo "    (already running)"

echo "==> Adding $USER to the libvirt group"
sudo usermod -aG libvirt "$USER"

echo
echo "==> Result"
sudo virsh net-list --all
ls -l /dev/kvm
echo
echo "DONE. You must log out and back in (or reboot) for the libvirt group to"
echo "take effect. Until then, virsh needs sudo."
