#!/usr/bin/env bash
# Step 9: install the udev rule that keeps the NI chassis attached to the VM.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Installing /usr/local/bin/ni-daq-usb"
sudo install -m 0755 "$HERE/ni-daq-usb" /usr/local/bin/ni-daq-usb

echo "==> Installing /etc/udev/rules.d/90-ni-daq-vm.rules"
sudo install -m 0644 "$HERE/90-ni-daq-vm.rules" /etc/udev/rules.d/90-ni-daq-vm.rules

echo "==> Reloading udev"
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb --action=add

echo "==> Clearing stale NI hostdevs and attaching what is present"
sudo /usr/local/bin/ni-daq-usb attach

echo "==> Log"
sudo tail -20 /var/log/ni-daq-usb.log 2>/dev/null || echo "  (no log yet)"
