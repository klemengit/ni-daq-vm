#!/usr/bin/env bash
# Build the NI-DAQmx driver VM from scratch.
#
#   ./setup.sh              run every stage in order
#   ./setup.sh <stage>...   run only these stages
#   ./setup.sh --list       show the stages
#
# Every stage is idempotent: re-running is safe. Stages that need root will
# prompt for sudo. The whole thing takes roughly 20-30 minutes, most of it
# downloading the Ubuntu image and the NI driver.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

VM=ni-daq
IP=192.168.122.50
KEY="$HOME/.ssh/ni_daq_vm"
IMG=ubuntu-24.04-cloudimg.img
IMG_URL=https://cloud-images.ubuntu.com/releases/noble/release/ubuntu-24.04-server-cloudimg-amd64.img
KVER=6.14.0-37          # lowest kernel >= NI's 6.11 minimum for Ubuntu 24.04
NI_RELEASE=2026Q3
GRPC_VER=v2.19.0

STAGES=(host-deps ufw image vm guest udev control client verify)

say()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ssh_vm() { ssh -i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
               -o LogLevel=ERROR "klemen@$IP" "$@"; }

wait_for_ssh() {
    say "Waiting for SSH on $IP"
    local i
    for i in $(seq 1 90); do
        ssh_vm -o ConnectTimeout=4 true 2>/dev/null && { echo "    up"; return 0; }
        printf '.'; ping -c1 -W3 "$IP" >/dev/null 2>&1
    done
    echo; echo "    timed out. Try: sudo virsh console $VM (password in .console-password)"
    return 1
}

stage_host_deps() {
    say "Host packages, libvirt, default network"
    ./host/01-install-host-deps.sh
}

stage_ufw() {
    say "Letting the guest network through ufw"
    # Omarchy enables ufw with default-deny incoming, which silently drops the
    # guest's DHCP. Must happen BEFORE the VM first boots.
    sudo ufw allow in on virbr0 to any port 67 proto udp comment 'libvirt DHCP'
    sudo ufw allow in on virbr0 to any port 53             comment 'libvirt DNS'
    sudo ufw route allow in on virbr0                      comment 'libvirt guest egress'
    sudo ufw status verbose | tail -8
}

stage_image() {
    say "Ubuntu 24.04 cloud image"
    if [ -f "$IMG" ]; then
        echo "    already present ($(du -h "$IMG" | cut -f1))"
    else
        curl -L --progress-bar -o "$IMG" "$IMG_URL"
    fi
}

stage_vm() {
    say "Creating the VM"
    if sudo virsh dominfo "$VM" &>/dev/null; then
        echo "    '$VM' exists; use: host/02-create-vm.sh --recreate"
    else
        ./host/02-create-vm.sh
    fi
    if ! grep -q "^Host $VM\$" "$HOME/.ssh/config" 2>/dev/null; then
        say "Adding '$VM' to ~/.ssh/config"
        cat >> "$HOME/.ssh/config" <<EOF

Host $VM
    HostName $IP
    User klemen
    IdentityFile $KEY
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
    LogLevel ERROR
EOF
    fi
    wait_for_ssh
}

stage_guest() {
    wait_for_ssh
    say "Guest: kernel $KVER, hold it, cap networkd-wait-online"
    if [ "$(ssh_vm 'uname -r')" = "${KVER}-generic" ]; then
        echo "    already on ${KVER}-generic"
    else
        scp -q -i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            guest/01-base.sh "klemen@$IP:/tmp/"
        ssh_vm "bash /tmp/01-base.sh $KVER" || true   # ends by rebooting
        wait_for_ssh
    fi

    say "Guest: NI-DAQmx $NI_RELEASE (DKMS build happens here)"
    if ssh_vm 'dpkg -l ni-daqmx 2>/dev/null | grep -q "^ii"'; then
        echo "    ni-daqmx already installed"
    else
        scp -q -i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            guest/02-nidaqmx.sh "klemen@$IP:/tmp/"
        ssh_vm "bash /tmp/02-nidaqmx.sh $NI_RELEASE"
    fi

    say "Guest: NI gRPC device server $GRPC_VER"
    scp -q -i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        guest/03-grpc-server.sh "klemen@$IP:/tmp/"
    ssh_vm "bash /tmp/03-grpc-server.sh $GRPC_VER"
}

stage_udev() {
    say "USB passthrough that survives the chassis changing its product id"
    ./host/04-install-udev-rule.sh
}

stage_control() {
    say "The ni-daq control command"
    mkdir -p "$HOME/.local/bin"
    ln -sf "$PWD/host/ni-daq" "$HOME/.local/bin/ni-daq"
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) echo "    ni-daq -> $HOME/.local/bin/ni-daq" ;;
        *) echo "    installed, but $HOME/.local/bin is not on your PATH" ;;
    esac
}

stage_client() {
    say "Client virtualenv on the host (no NI software installed here)"
    command -v uv >/dev/null || { echo "    uv not found: install it first"; return 1; }
    uv venv --python 3.12 .venv
    uv pip install --python .venv/bin/python -r client/requirements.txt
    .venv/bin/python -c "import nidaqmx, LDAQ; print(f'    nidaqmx {nidaqmx.__version__}, LDAQ {LDAQ.__version__}')"
}

stage_verify() {
    say "End-to-end check"
    .venv/bin/python client/smoke_test.py
    cat <<'EOM'

    Reading of the result:
      devices listed         -> everything works
      DaqError -200220       -> everything works, no hardware attached
      NOT_FOUND DAQmx...     -> driver missing in the guest, or the gRPC server
                                needs a restart after the driver install
      transport FAILED       -> VM down, or ufw
EOM
}

if [ "${1:-}" = "--list" ]; then printf '%s\n' "${STAGES[@]}"; exit 0; fi

RUN=("$@"); [ ${#RUN[@]} -eq 0 ] && RUN=("${STAGES[@]}")
for s in "${RUN[@]}"; do
    fn="stage_${s//-/_}"
    declare -F "$fn" >/dev/null || { echo "unknown stage: $s (see --list)"; exit 1; }
    "$fn"
done
say "Done."
