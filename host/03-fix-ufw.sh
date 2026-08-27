#!/usr/bin/env bash
# Step 3: let libvirt's NAT network through ufw.
#
# Omarchy ships ufw enabled with default-deny incoming. DHCP requests from the
# guest arrive on virbr0 as broadcast to UDP/67 on the host, and ufw drops them
# before dnsmasq sees them -- so the guest never gets a lease and never comes up.
# Idempotent: ufw ignores duplicate rules.
set -euo pipefail

VM=ni-daq
IP=192.168.122.50

echo "==> ufw before"
sudo ufw status verbose | head -10

echo
echo "==> Allowing DHCP and DNS from the virtual network"
sudo ufw allow in on virbr0 to any port 67 proto udp comment 'libvirt DHCP'
sudo ufw allow in on virbr0 to any port 53             comment 'libvirt DNS'

echo "==> Allowing the guest to route out to the internet"
sudo ufw route allow in on virbr0 comment 'libvirt guest egress'

echo
echo "==> ufw after"
sudo ufw status verbose

echo
echo "==> Watching for DHCP traffic while the guest reboots"
sudo timeout 75 tcpdump -l -n -i virbr0 -c 8 'port 67 or port 68' &
TCPDUMP_PID=$!
sleep 2
sudo virsh reset "$VM"
wait $TCPDUMP_PID 2>/dev/null || true

echo
echo "==> Lease table"
sudo virsh net-dhcp-leases default

echo "==> Reachability"
if ping -c2 -W3 "$IP" >/dev/null 2>&1; then
    echo "    $IP responds to ping"
    ssh -i "$HOME/.ssh/ni_daq_vm" -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 klemen@"$IP" \
        'echo "    kernel: $(uname -r)"; echo "    release: $(lsb_release -ds)"; ip -brief addr' \
        || echo "    ping works but ssh does not -- check cloud-init inside the guest"
else
    echo "    $IP still unreachable"
fi
