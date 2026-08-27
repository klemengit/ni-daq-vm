#!/usr/bin/env bash
# Runs INSIDE the ni-daq guest. Prepares the OS for the NI driver:
#   - install and hold a kernel NI actually supports
#   - stop anything from moving that kernel later
#   - stop a network failure from hanging the boot forever
set -euo pipefail

KVER="${1:-6.14.0-37}"          # lowest available >= NI's 6.11 minimum for 24.04

echo "==> Current kernel: $(uname -r)"

echo "==> Installing kernel $KVER and headers"
sudo apt-get -qq update
sudo apt-get -y install \
    "linux-image-${KVER}-generic" \
    "linux-headers-${KVER}-generic" \
    "linux-modules-extra-${KVER}-generic" \
    dkms build-essential curl ca-certificates unzip

echo "==> Holding the kernel so apt can never move it"
sudo apt-mark hold \
    "linux-image-${KVER}-generic" \
    "linux-headers-${KVER}-generic" \
    "linux-modules-extra-${KVER}-generic" \
    linux-generic linux-image-generic linux-headers-generic \
    linux-image-generic-hwe-24.04 linux-headers-generic-hwe-24.04 2>/dev/null || true
apt-mark showhold

echo "==> Booting $KVER by default"
# Pick the exact menu entry rather than trusting "newest wins".
MENU="gnulinux-advanced-$(sudo grub-probe --target=fs_uuid /boot 2>/dev/null || true)"
sudo sed -i 's/^GRUB_DEFAULT=.*/GRUB_DEFAULT=saved/' /etc/default/grub
sudo grub-set-default "Advanced options for Ubuntu>Ubuntu, with Linux ${KVER}-generic" || true
sudo update-grub

echo "==> Capping systemd-networkd-wait-online (was: no limit)"
sudo install -d /etc/systemd/system/systemd-networkd-wait-online.service.d
sudo tee /etc/systemd/system/systemd-networkd-wait-online.service.d/zz-timeout.conf >/dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/lib/systemd/systemd-networkd-wait-online --timeout=30
EOF
sudo systemctl daemon-reload

echo
echo "==> Rebooting into $KVER"
sudo systemctl reboot || true
