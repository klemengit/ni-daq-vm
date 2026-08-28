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

echo "==> Optional: passwordless 'ni-daq attach'"
# A cold `ni-daq up` needs no privileges -- libvirt attaches the chassis from
# the domain XML. This only removes the prompt when reconciling a chassis
# plugged in mid-session. Validate before installing: a malformed file in
# /etc/sudoers.d can lock you out of sudo entirely.
TMP=$(mktemp)
sed "s/__USER__/$USER/" "$HERE/sudoers-ni-daq.tmpl" > "$TMP"
if sudo visudo -cqf "$TMP"; then
    sudo install -m 0440 -o root -g root "$TMP" /etc/sudoers.d/ni-daq
    echo "    installed /etc/sudoers.d/ni-daq for $USER"
else
    echo "    REFUSED: generated sudoers file did not validate, nothing installed"
fi
rm -f "$TMP"
