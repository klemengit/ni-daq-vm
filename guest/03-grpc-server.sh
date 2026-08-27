#!/usr/bin/env bash
# Runs INSIDE the ni-daq guest. Installs NI's gRPC device server as a service.
set -euo pipefail

VER="${1:-v2.19.0}"
ASSET="ni-grpc-device-server-linux-glibc2_38-x64.tar.gz"
DIR=/opt/ni-grpc-device-server

echo "==> Downloading grpc-device $VER"
curl -sSL -o /tmp/grpc.tar.gz \
    "https://github.com/ni/grpc-device/releases/download/${VER}/${ASSET}"

echo "==> Installing to $DIR"
sudo rm -rf "$DIR" && sudo mkdir -p "$DIR"
sudo tar xzf /tmp/grpc.tar.gz -C "$DIR"        # NB: flat tarball, no strip-components
sudo chown -R root:root "$DIR"
sudo chmod +x "$DIR/ni_grpc_device_server"

echo "==> Binding to all interfaces (ships as [::1], loopback only)"
sudo python3 - "$DIR/server_config.json" <<'PY'
import json, sys
p = sys.argv[1]
cfg = json.load(open(p))
cfg["address"] = "[::]"
json.dump(cfg, open(p, "w"), indent=4)
print(json.dumps(cfg, indent=4))
PY

echo "==> systemd unit"
sudo tee /etc/systemd/system/ni-grpc-device.service >/dev/null <<EOF
[Unit]
Description=NI gRPC Device Server
Documentation=https://github.com/ni/grpc-device
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$DIR
ExecStart=$DIR/ni_grpc_device_server
Restart=on-failure
RestartSec=5
# root: the DAQmx driver needs access to the NI device nodes
User=root

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now ni-grpc-device.service
sleep 3 2>/dev/null || true

echo
echo "==> Status"
systemctl --no-pager --lines=15 status ni-grpc-device.service || true
echo
echo "==> Listening sockets"
ss -tlnp 2>/dev/null | grep -E "31763|50055" || echo "    NOT LISTENING"
