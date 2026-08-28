#!/usr/bin/env bash
# Step 2: create the headless Ubuntu 24.04 driver VM.
# Idempotent-ish: refuses to clobber an existing domain (use --recreate to replace).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VM=ni-daq
MAC=52:54:00:4e:49:01          # 4e:49 = "NI"
IP=192.168.122.50
RAM=3072
VCPUS=2
DISK_GB=24
IMG_SRC="$HERE/ubuntu-24.04-cloudimg.img"
IMG_DST=/var/lib/libvirt/images/${VM}.qcow2
PUBKEY="$HOME/.ssh/ni_daq_vm.pub"

[ -f "$IMG_SRC" ] || { echo "missing $IMG_SRC"; exit 1; }
[ -f "$PUBKEY" ]  || { echo "missing $PUBKEY"; exit 1; }

if [[ "${1:-}" == "--recreate" ]]; then
    sudo virsh destroy  "$VM" 2>/dev/null || true
    sudo virsh undefine "$VM" --nvram --remove-all-storage 2>/dev/null || true
elif sudo virsh dominfo "$VM" &>/dev/null; then
    echo "Domain '$VM' already exists. Re-run with --recreate to replace it."
    exit 1
fi

echo "==> Rendering cloud-init user-data"
USERDATA="$HERE/cloud-init/user-data.yaml"
NETCONF="$HERE/cloud-init/network-config.yaml"
sed "s|__SSH_PUBKEY__|$(cat "$PUBKEY")|" "$HERE/cloud-init/user-data.yaml.tmpl" > "$USERDATA"

echo "==> Preparing disk ($DISK_GB G)"
sudo install -d -m 0711 /var/lib/libvirt/images
sudo cp --reflink=auto "$IMG_SRC" "$IMG_DST"
sudo qemu-img resize "$IMG_DST" "${DISK_GB}G"
sudo chown root:root "$IMG_DST"

echo "==> Pinning $IP to $MAC on the default network"
sudo virsh net-update default delete ip-dhcp-host \
     "<host mac='$MAC'/>" --live --config 2>/dev/null || true
sudo virsh net-update default add ip-dhcp-host \
     "<host mac='$MAC' name='$VM' ip='$IP'/>" --live --config

echo "==> Creating the domain"
sudo virt-install \
    --name "$VM" \
    --memory "$RAM" --vcpus "$VCPUS" \
    --cpu host-passthrough \
    --disk "path=$IMG_DST,format=qcow2,bus=virtio" \
    --import \
    --os-variant ubuntu24.04 \
    --network "network=default,mac=$MAC,model=virtio" \
    --graphics none \
    --console pty,target_type=serial \
    --cloud-init "user-data=$USERDATA,network-config=$NETCONF,disable=on" \
    --noautoconsole

echo "==> Autostart at boot"
# Deliberately NOT autostarted: the VM holds ~2 GB of RAM and is only useful
# with the chassis plugged in. Bring it up with `ni-daq up` when measuring.
sudo virsh autostart --disable "$VM" 2>/dev/null || true

echo
echo "==> Waiting for SSH on $IP (cloud-init takes ~60-90 s on first boot)"
for i in $(seq 1 60); do
    if ssh -i "$HOME/.ssh/ni_daq_vm" -o StrictHostKeyChecking=no \
           -o UserKnownHostsFile=/dev/null -o ConnectTimeout=3 \
           klemen@"$IP" true 2>/dev/null; then
        echo "    SSH is up."
        break
    fi
    printf '.'; sleep 5
done
echo

echo "==> Guest info"
ssh -i "$HOME/.ssh/ni_daq_vm" -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null klemen@"$IP" \
    'echo "kernel: $(uname -r)"; echo "release: $(lsb_release -ds)"; free -m | head -2'

cat <<EOM

DONE.
  ssh ni-daq        (after step 3 adds the ssh config entry)
  ssh -i ~/.ssh/ni_daq_vm klemen@$IP
  sudo virsh console $VM     # serial console, escape with Ctrl-]
EOM
