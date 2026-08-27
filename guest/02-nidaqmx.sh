#!/usr/bin/env bash
# Runs INSIDE the ni-daq guest. Installs NI-DAQmx from NI's apt repo.
#
# `python -m nidaqmx installdriver` finds the right package but fails at the last
# step: it unpacks into a 0700 root-owned tempdir that apt cannot read, giving
#   E: Unsupported file /tmp/tmpXXXX/.../ni-ubuntu2404-drivers-2026Q3.deb
# So we do the same thing by hand from a readable directory.
set -euo pipefail

REL="${1:-2026Q3}"
URL="https://download.ni.com/support/softlib/MasterRepository/LinuxDrivers${REL}/NILinux${REL}DeviceDrivers.zip"
D=~/nidriver

echo "==> Fetching NI Linux Device Drivers ${REL}"
mkdir -p "$D" && cd "$D"
[ -f "NILinux${REL}DeviceDrivers.zip" ] || curl -sSL -o "NILinux${REL}DeviceDrivers.zip" "$URL"
unzip -o -q "NILinux${REL}DeviceDrivers.zip"

echo "==> Registering NI's apt repository"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "./ni-ubuntu2404-drivers-${REL}.deb"
sudo apt-get -qq update

echo "==> Installing ni-daqmx (this is a large download)"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ni-daqmx

echo "==> DKMS status"
dkms status || true

echo "==> Kernel modules built for $(uname -r)"
find /lib/modules/"$(uname -r)"/updates/dkms -name "ni*" 2>/dev/null | head -20 || echo "    none found"
