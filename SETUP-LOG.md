# NI-DAQmx on Omarchy via an Ubuntu driver VM — setup log

**Goal:** run NI USB DAQ hardware from Omarchy (Arch) without installing anything
NI-specific on Arch. Ubuntu runs headless in a VM, owns the USB device and the NI
kernel modules, and exposes the driver over the network. Python (LDAQ) stays on
Arch.

Started 2026-08-27.

---

## Why a VM and not a container

NI-DAQmx installs DKMS kernel modules (`nikal`, `nipalk`, ...) that claim the USB
device. Containers share the host kernel, so those modules would have to build
against Arch's kernel — currently `7.1.9-arch1-2`. NI's module source already
fails on Ubuntu's 6.5 HWE and 6.14 kernels, so this is a non-starter. A VM gives
the driver its own kernel, which is the only thing it actually needs.

Second benefit: the Ubuntu kernel must be **pinned** (held back) or a routine
`apt upgrade` breaks the DKMS build. Doing that inside a throwaway VM is fine;
doing it on your daily-driver laptop is not.

## Architecture

```
Omarchy (Arch)                          Ubuntu 24.04 VM (headless)
------------------                      --------------------------
Python + LDAQ                           ni_grpc_device_server  :31763
nidaqmx[grpc]  ──── gRPC over ────────► NI-DAQmx runtime
(no NI driver)      libvirt NAT         nikal / nipalk kernel modules
                                                 │
                                                 │ USB passthrough (vendor 0x3923)
                                                 ▼
                                            NI USB chassis
```

- **NI gRPC Device Server** (`ni/grpc-device`) is NI's own open-source server that
  exposes the DAQmx C API over gRPC. Default port **31763**. NI-DAQmx is tested
  through 2026 Q1.
- **`nidaqmx[grpc]`** on the Arch side speaks that protocol natively via
  `GrpcSessionOptions`. Same API as local use, one extra constructor argument.

## Known open risk: LDAQ

`LDAQ.national_instruments` delegates to `ladisk/nidaqwrapper`, which constructs
`nidaqmx.Task()` directly and does **not** plumb `grpc_options` through. Two ways
out, in order of preference:

1. `AITask.from_task(raw_task)` — documented raw-task injection. Build the
   `nidaqmx.Task(grpc_options=...)` ourselves, configure channels, wrap it.
   Costs nothing if it works.
2. Patch `nidaqwrapper` to accept `grpc_options` and pass it down. Klemen has
   confirmed this is acceptable (it is LADISK's own package).

**This link is unverified and is the main risk in the whole plan.** It gets
tested against an NI *simulated* device before real hardware is involved.

## Host survey (2026-08-27)

| Item | Result |
|---|---|
| CPU | i7-1165G7, VT-x present |
| `/dev/kvm` | present, mode 0666 |
| Kernel | 7.1.9-arch1-2 |
| libvirt / qemu / virt-manager | **all missing** — nothing installed |
| `libvirtd` | inactive, unit not found |
| User groups | `klemen docker input wheel` — not in `libvirt` |
| Free space on `/home` | 98 G |
| Outbound network from shell | OK (HTTP 200) |
| NI USB device attached | no — none connected during setup |
| `sudo` | requires a password; privileged steps are run by hand |

Note: `docker` is installed. Docker sets the iptables `FORWARD` policy to `DROP`,
which can break libvirt's NAT network and stop the guest reaching the internet.
Flagged here in case guest `apt` fails later.

## Plan

1. Install the libvirt/QEMU stack on the host. *(privileged)*
2. Build an Ubuntu 24.04 LTS VM from the official cloud image, headless,
   provisioned by cloud-init. 24.04's GA kernel (6.8) is the newest one NI is
   known good on; 26.04 is ahead of what NI has caught up to.
3. In the guest: install NI-DAQmx, run `dkms autoinstall`, **hold the kernel**.
4. In the guest: install `ni_grpc_device_server`, bind it to `[::]`, run it as a
   systemd unit.
5. Create an NI simulated device and verify the gRPC path end to end from Arch.
6. Verify LDAQ over gRPC against the simulated device. Patch `nidaqwrapper` if
   `from_task()` is not enough.
7. Only then: attach the real chassis and set up USB passthrough.

---

# Running log

## Step 0 — project scaffold

```
~/Work/ni-daq-vm/
├── SETUP-LOG.md      this file
├── host/             scripts that run on Arch
└── provision/        cloud-init and guest-side scripts
```

## Step 1 — host survey and dependency script

`sudo` on this machine requires a password, so every privileged step is written
as a script under `host/` and run by hand rather than executed by the agent.

`host/01-install-host-deps.sh` installs `qemu-desktop libvirt virt-install
virt-manager dnsmasq edk2-ovmf libisoburn openbsd-netcat`, enables
`libvirtd.socket`, starts and autostarts the default NAT network, and adds
`klemen` to the `libvirt` group. **Not yet run.**

## Step 2 — VM definition

Ubuntu 24.04 cloud image downloaded (596 MB, build dated 2026-08-26) to
`ubuntu-24.04-cloudimg.img`.

Chosen shape, in `host/02-create-vm.sh`:

| Setting | Value | Why |
|---|---|---|
| Name | `ni-daq` | |
| RAM / vCPU | 3 GB / 2 | driver + gRPC server only; no desktop |
| Disk | 24 G qcow2, thin | NI-DAQmx is a few GB |
| MAC | `52:54:00:4e:49:01` | `4e:49` spells "NI" |
| IP | `192.168.122.50`, static DHCP lease | so the gRPC address never moves |
| Graphics | none, serial console | headless appliance |
| Autostart | yes (`virsh autostart`) | invisible after boot |

A dedicated SSH key `~/.ssh/ni_daq_vm` was generated (no passphrase — this is a
local-only appliance reachable solely over libvirt's NAT). Cloud-init creates the
`klemen` user, installs `dkms`/`build-essential`, and **disables
unattended-upgrades and the apt daily timers**, so nothing can move the kernel
out from under DKMS later.

## Corrections to the original plan

Two things found during setup that invalidate assumptions made earlier:

### 1. NI simulated devices do not exist on Linux

Simulated devices are a NI MAX feature, and NI MAX is Windows-only. There is no
way to define one via `nidaqmxconfig` on Linux. **The "validate against a fake
card before touching hardware" step is not possible.**

Replacement validation strategy — still useful without hardware:

- `System.remote(grpc_options).devices` over gRPC from Arch proves the transport,
  the server and the loaded driver are all working. It just returns an empty list.
- Creating a task and calling `add_ai_voltage_chan("Dev1/ai0")` with no hardware
  attached returns DAQmx error **-200220 (device identifier invalid)**. That error
  originating *from the remote driver* proves the entire chain end to end. The
  distinction that matters:
  - `-200220` → plumbing is correct, only the device is absent. Good.
  - transport / `DaqNotFoundError` / import error → plumbing is broken.
- The same trick validates the **LDAQ** path: if `AITask.from_task(raw_task)` +
  `NIAcquisition` reaches `-200220`, the `grpc_options` plumbing carried through.

So everything except real signal flow can be verified before the chassis arrives.

### 2. Ubuntu 24.04 needs the HWE kernel, not the GA kernel

Earlier I assumed 24.04's GA kernel (6.8) was the safe target. It is not. The
[NI Linux Device Drivers 2026 Q2 compatibility table](https://www.ni.com/en/support/documentation/compatibility/26/ni-linux-device-drivers-2026-q2-compatibility.html)
gives **minimum** kernels:

| OS | Minimum kernel |
|---|---|
| Ubuntu 22.04 | 6.5 |
| Ubuntu 24.04 | **6.11** |
| RHEL 9.6 / 10.0 | 5.14 / 6.12 |
| openSUSE Leap 15.6 / 16.0 | 6.4 / 6.11 |

Supported distros are RHEL 9.6/10.0, openSUSE Leap 15.6/16.0, Ubuntu LTS 22.04
and 24.04 only.

The cloud image ships the GA kernel (6.8), which is **below** NI's minimum, so
provisioning has to install an HWE kernel. Complication: 24.04's HWE track has
moved on to 6.14+, and there are reports of `nipalk` DKMS build failures on
6.14 — though that thread is marked solved and 2026 Q2 postdates it.

Approach: install HWE, attempt the DKMS build, and check empirically. If it
fails, step down to a 6.11-series kernel explicitly and hold it there. Either
way the kernel gets held once it works. Record the working combination below.

### 3. The NI driver .deb has to be fetched by hand

`download.ni.com` URL patterns were probed and all returned 404; the download
page is JavaScript-driven and gated. The repo-registration `.deb`
(`ni-ubuntu2404-drivers-<version>.deb`) must be downloaded from
<https://www.ni.com/en/support/downloads/drivers/download.ni-linux-device-drivers.html>
in a browser and dropped into this directory. Versions offered in the page's
dropdown go up to **2026 Q3**.

### 4. DMA remapping — probably a non-issue here

NI notes that Ubuntu 24.04 enables DMA remapping by default and that many NI
drivers do not support it (fix: `intel_iommu=off` on the kernel command line).
Inside a VM with no virtual IOMMU there is nothing to remap, so this should not
bite. Noted in case device enumeration misbehaves later.

## Step 2 (first attempt) — VM boots, but has no network

`host/01-install-host-deps.sh` and `host/02-create-vm.sh` both ran cleanly. The
`Domain is still running. Installation may be in progress.` message from
virt-install is normal for `--import` — there is no installer, it just boots the
disk. But the SSH wait loop never succeeded.

### What was verified

| Check | Result |
|---|---|
| `virsh list` | `ni-daq` running (and virsh works without sudo, so the group took effect) |
| Guest boot | reaches `ni-daq login:` — Ubuntu **24.04.4 LTS**, cloud-init v26.1 completed |
| Host bridge `virbr0` | UP, `192.168.122.1/24` |
| Guest tap `vnet0` | present, MAC `fe:54:00:4e:49:01` (host side of the guest NIC) |
| Domain XML `<interface>` | correct: virtio, `network='default'`, right MAC |
| `dnsmasq` | running, read the static hostsfile before the guest booted |
| DHCP journal | **no DHCPDISCOVER at all** |
| `virbr0.status` lease file | 0 bytes |
| `ip neigh` for .50 | `FAILED` (never answered ARP) |

So the host side is entirely correct and the guest simply never asked for an
address. The fault is inside the guest's network configuration.

Capturing the serial console needs a pty; `virsh console` refuses without one:

```bash
timeout 25 script -q -c "virsh -c qemu:///system console ni-daq --force" console.log </dev/null
```

### Two mistakes in the first cloud-init

1. **No `network-config` in the seed.** The NoCloud datasource was given only
   `user-data`, leaving cloud-init to guess via its fallback path. Replaced with
   an explicit netplan v2 config (`provision/network-config.yaml`) that DHCPs any
   `en*` interface and identifies by MAC.
2. **`lock_passwd: true` made the VM undebuggable.** With key-only access and no
   network, there was no way in at all — the serial console was sitting at a
   login prompt that could not be satisfied. Now a random console password is
   generated into `.console-password` (mode 0600, git-ignored) and set on the
   `klemen` account. `ssh_pwauth` stays `false`, so that password works **only on
   the serial console**, not over the network.

Lesson worth keeping: a headless VM needs a console login that works before the
network does.

### Also changed

`package_update`/`packages` were removed from cloud-init. First boot is now
identity + console access only, and all apt work moves to a script run over SSH
in step 3. Faster boot, and a failing apt no longer hides inside cloud-init.

Recreate with:

```bash
host/02-create-vm.sh --recreate
```

## Step 3 — root cause: ufw was eating the guest's DHCP

The second attempt (explicit netplan + console password) failed **identically**,
which was the clue: if two different guest network configurations produce exactly
the same symptom, the guest is not the problem.

Serial console showed the guest hung at:

```
[ ** ] Job systemd-networkd-wait-online.se…start running (1min 7s / no limit)
```

so the guest *was* trying to bring up networking and getting nothing back.

Host firewall check:

```
ufw 0.36.2-7    systemctl is-active ufw -> active
```

**Omarchy ships `ufw` enabled with default-deny incoming.** The guest's
DHCPDISCOVER is a broadcast to UDP/67 on the host via `virbr0`. ufw drops it
before dnsmasq ever sees it — which is exactly consistent with every observation:
dnsmasq bound and healthy, zero DHCP log lines, empty lease file, ARP FAILED.

This is not libvirt's fault and not the guest's. libvirt does install its own
allow rules, but ufw's nftables tables are evaluated too, and a drop in any table
wins.

No `omarchy-*` firewall helper exists (checked), so plain ufw rules — in
`host/03-fix-ufw.sh`:

```bash
ufw allow in on virbr0 to any port 67 proto udp   # DHCP
ufw allow in on virbr0 to any port 53             # DNS via dnsmasq
ufw route allow in on virbr0                      # guest -> internet (forwarding)
```

Host→guest traffic (SSH, and later gRPC on 31763) needs no rule: it is *outgoing*
from the host, which ufw allows by default, and the replies are ESTABLISHED.

The script then proves the fix rather than assuming it — it runs `tcpdump` on
`virbr0` filtered to ports 67/68, resets the domain, and prints the lease table
and an SSH probe. If DHCP packets appear and a lease is issued, the diagnosis was
right.

### Follow-up to apply inside the guest later

`systemd-networkd-wait-online` is running with **no timeout**, so any future
network problem hangs the boot forever. For an appliance that should never
happen. Step 4 will cap it.

### ufw fix confirmed

`host/03-fix-ufw.sh` worked on the first run:

```
dnsmasq-dhcp: DHCPACK(virbr0) 192.168.122.50 52:54:00:4e:49:01 ni-daq
```

The script's own reachability probe still said "unreachable" only because it ran
seconds after `virsh reset`, before the guest had finished booting — a flaw in
the script, not in the fix. `tcpdump` is also not installed on this host, so that
part of the check was skipped harmlessly.

Confirmed ufw rule set:

```
67/udp on virbr0    ALLOW IN   Anywhere    # libvirt DHCP
53    on virbr0     ALLOW IN   Anywhere    # libvirt DNS
Anywhere            ALLOW FWD  Anywhere on virbr0   # libvirt guest egress
```

**One surprise:** the domain then powered itself off. That is virt-install's
normal `--cloud-init ...,disable=on` flow — it boots once with the seed ISO, and
on shutdown detaches the ISO and writes `/etc/cloud/cloud-init.disabled` in the
guest (`cloud-init status` now reports `disabled`). A plain `virsh start` brings
it up for good, and `virsh autostart` is already set, so this happens once and
never again.

## Step 4 — guest base: the kernel

Guest as first booted: **6.8.0-138-generic**, below NI's 6.11 minimum.

`apt-cache policy linux-image-generic-hwe-24.04` now resolves to **7.0.0** —
far beyond anything NI has tested — so the HWE metapackage is unusable. No
6.11-series kernel is left in the archive either. Available `-generic` kernels
are 6.14.x and 6.17.x.

Chose **6.14.0-37**: the lowest available at or above NI's stated minimum. NI's
drivers break on kernels that are too *new*, so the oldest supported one is the
safest starting point.

`provision/04-guest-base.sh` installs that kernel plus headers and
`linux-modules-extra`, then:

- `apt-mark hold` on the specific kernel packages **and** on `linux-generic`,
  `linux-image-generic`, `linux-headers-generic`, and both HWE metapackages, so
  nothing can drag the guest onto a newer kernel.
- `GRUB_DEFAULT=saved` + `grub-set-default` pinned to that exact menu entry,
  rather than trusting "newest wins".
- Caps `systemd-networkd-wait-online` at 30 s via a drop-in. It was running with
  **no limit**, which is how a network problem turns into a permanently hung
  boot.

Verified after reboot: `6.14.0-37-generic`, headers present at
`/usr/src/linux-headers-6.14.0-37-generic`, `dkms-3.0.11`, 8 packages held.

## Step 5 — NI gRPC device server

`ni/grpc-device` **v2.19.0** (2026-06-30),
asset `ni-grpc-device-server-linux-glibc2_38-x64.tar.gz` (15.6 MB). Ubuntu 24.04
ships glibc 2.39, so the glibc 2.38 build is fine.

Gotcha: the tarball is **flat** — no top-level directory. `--strip-components=1`
silently extracts nothing and leaves an empty directory. Extract without it.

`provision/05-grpc-server.sh` installs to `/opt/ni-grpc-device-server`, rewrites
`server_config.json` (**ships as `"address": "[::1]"`, loopback only** — must be
`"[::]"` to accept connections from the host), and installs a systemd unit
`ni-grpc-device.service`, enabled at boot and running as root because the DAQmx
driver needs the NI device nodes.

Running and listening:

```
Server listening on port 31763
LISTEN 0 4096 *:31763 *:*
```

It warns that credentials are insecure on a non-loopback address. Accepted: the
only route to this port is libvirt's host-only NAT, which is not reachable from
outside the laptop.

## Step 6 — Arch side proves the chain

`uv venv` + `nidaqmx[grpc]` → **nidaqmx 1.6.0**, grpcio 1.83.0. It imports
cleanly on Arch with **no NI driver installed**, which was the whole premise.

`host/smoke_test.py` result:

```
transport : OK (gRPC channel ready)
driver    : RpcError NOT_FOUND: Could not find DAQmxGetSystemInfoAttribute.
task      : RpcError NOT_FOUND: Could not find DAQmxCreateTask.
```

This is exactly the expected pre-driver state, and it is a real result:
Arch reaches the server, the server accepts the RPC and tries to dispatch it —
and only then fails, because NI-DAQmx is not installed in the guest yet. Every
layer except the driver itself is now proven.

### Finding: for NI-DAQmx the gRPC session name *is* the task name

First attempt used `session_name="smoke"` for everything and got:

```
DaqError -1: Unsupported session name: "smoke".
            If a session name is specified, it must match the task name.
```

So `GrpcSessionOptions.session_name` must either be empty (system-level calls) or
exactly equal to the task's name. `smoke_test.py` now builds options per call via
`opts_for(session_name)`.

**This matters for the LDAQ integration.** Whatever `grpc_options` we eventually
plumb through `nidaqwrapper` must carry a `session_name` matching the DAQmx task
name that LDAQ creates — a single shared `GrpcSessionOptions` object reused
across differently-named tasks will fail. Cleanest fix in `nidaqwrapper` is to
accept the channel and build the options internally per task, rather than
accepting a prebuilt `GrpcSessionOptions`.

## Current state

| Layer | Status |
|---|---|
| Host virt stack | done |
| ufw | fixed and verified |
| VM `ni-daq`, autostart, static 192.168.122.50 | done, `ssh ni-daq` works |
| Guest kernel 6.14.0-37, held, headers, DKMS | done |
| gRPC server, systemd, listening on 31763 | done |
| Arch `nidaqmx[grpc]` venv + smoke test | done |
| **NI-DAQmx driver in the guest** | **blocked — .deb not downloaded** |
| LDAQ over gRPC | not started (needs the driver) |
| USB passthrough | not started (no hardware attached yet) |

### Next

1. Download `ni-ubuntu2404-drivers-<version>.deb` from
   <https://www.ni.com/en/support/downloads/drivers/download.ni-linux-device-drivers.html>
   into this directory.
2. Install it in the guest, `apt install ni-daqmx`, `dkms autoinstall`. **The
   DKMS build against 6.14 is the moment of truth.** If `nipalk` fails to build,
   try 6.17, then 6.8, and record which works.
3. Re-run `host/smoke_test.py` — expect the driver version to print and
   `-200220` for the task, instead of NOT_FOUND.
4. Then LDAQ, then USB passthrough with real hardware.

## Step 7 — driver install, the easy way

The browser gate on ni.com turned out to be avoidable entirely: **nidaqmx-python
ships a driver installer**.

```bash
python -m nidaqmx installdriver     # subcommand, not --installdriver
```

It detects the distribution, downloads NI's repo-registration package and installs
the driver through apt. So the manual `.deb` download is unnecessary — scratch
that blocker.

Done inside the guest in a venv (Ubuntu 24.04 is PEP 668 / externally managed, so
no system-wide pip):

```bash
sudo apt-get install -y python3-venv
python3 -m venv ~/nivenv
~/nivenv/bin/pip install nidaqmx          # 1.6.0
yes | sudo DEBIAN_FRONTEND=noninteractive ~/nivenv/bin/python -m nidaqmx installdriver
```

`installdriver` takes no options and prompts, hence `yes |`. It needs root because
it drives apt. Note this is the *guest's* copy of nidaqmx, used only as a driver
installer — the Arch-side copy is the actual client.

### `installdriver` fails at the last step — and the workaround

`python -m nidaqmx installdriver` correctly resolved **NI-DAQmx 26.5.0 (2026 Q3)**
and downloaded it, then died on:

```
E: Unsupported file /tmp/tmph6iyybe6/NILinux2026Q3DeviceDrivers/ni-ubuntu2404-drivers-2026Q3.deb
Error: An error occurred while installing the NI-DAQmx driver. Command returned non-zero exit status '100'.
```

The `.deb` is valid (`file` confirms format 2.0). The problem is that the
installer unpacks into a `tempfile.mkdtemp()` directory, which is mode `0700` and
root-owned; apt drops privileges and cannot read it. Installing the identical
file from a normal directory works.

The download URL is not gated at all — it is in the package's own
`nidaqmx/_installer_metadata.json`:

```
https://download.ni.com/support/softlib/MasterRepository/LinuxDrivers2026Q3/NILinux2026Q3DeviceDrivers.zip
```

2026 Q3 lists support for ubuntu 22.04, ubuntu 24.04, rhel 9, rhel 10,
opensuse 15.6, opensuse 16.0. `provision/07-install-nidaqmx.sh` automates the
whole thing.

### The DKMS build succeeded

**All 32 NI DKMS modules built against 6.14.0-37**, `nipalk` included — the exact
module that fails for people on 6.14 with older driver releases. 2026 Q3 handles
it.

Loaded modules: `nikal nipalk nidimk niorbk nimdbgk nimxdfk nipxirmk`
Runtime: `/usr/lib/x86_64-linux-gnu/libnidaqmx.so.26.5.0`

**Working combination — do not drift from this:**

| Component | Version |
|---|---|
| Ubuntu | 24.04.4 LTS |
| Kernel | 6.14.0-37-generic (held) |
| NI-DAQmx | 26.5.0 / 2026 Q3 |
| grpc-device | v2.19.0 |
| nidaqmx-python (Arch client) | 1.6.0 |

### Gotcha: restart the gRPC server after installing the driver

Immediately after the driver install the smoke test still reported
`NOT_FOUND: Could not find DAQmxGetSystemInfoAttribute` — the server resolves the
DAQmx symbols once at startup, and it had been started before the driver existed.
`systemctl restart ni-grpc-device` fixed it. Only relevant when installing or
upgrading the driver under a running server; on boot, ordering makes it a
non-issue.

## Step 8 — CHAIN CONFIRMED

`host/smoke_test.py` from Omarchy, with no NI software installed on Arch:

```
transport : OK (gRPC channel ready)
driver    : NI-DAQmx DriverVersion(major_version=26, minor_version=5, update_version=0)
devices   : none attached
task      : DaqError -200220 (chain OK, no hardware)
              Device identifier is invalid.
```

`-200220` arriving from the remote driver is the predicted success signal without
hardware. **Arch → gRPC → Ubuntu → NI-DAQmx works.** Everything except real
signal flow is now proven.

## Step 9 — LDAQ over gRPC: no patch needed after all

The one unverified link turned out to need no code changes. Reading the installed
sources (`nidaqwrapper` 0.2.0, `LDAQ` 1.3.2):

**`AITask.__init__` cannot work remotely** — `ai_task.py:97` calls
`nidaqmx.system.System.local()` outright, as do `ao_task.py:112`, several places
in `digital.py`, and five helpers in `utils.py`.

**But `AITask.from_task()` sidesteps all of it.** It builds the instance with
`object.__new__(cls)`, never calling `__init__`, and populates every attribute by
reading the live task (`task.devices`, `task.timing...`). Given a remote task,
those reads go over gRPC.

**And `NIAcquisition` accepts an `AITask` object directly**
(`acquisition.py:82-83`). It only calls `get_task_by_name()` — which does hit the
local system — when handed a *task-name string* from NI MAX. Passing the object
avoids that path entirely.

`_require_nidaqmx()` only checks that the `nidaqmx` **package** imports, not that
a driver is present, so it passes on Arch.

### Verified without hardware

`host/ldaq_grpc_test.py`:

```
1. nidaqmx 1.6.0 imports on Arch with no NI driver
2. _require_nidaqmx() OK
3. driver  : NI-DAQmx 26.5.0   devices : none attached
4. remote task created: name 'ldaq_probe', ai_channels 0
5. AITask.from_task() -> ValueError: Task has no AI channels.
      reached the channel check => it read ai_channels over gRPC
      => __init__ and its System.local() call were correctly skipped
6. add_ai_voltage_chan("Dev1/ai0") -> DaqError -200220 (no device attached)
```

Step 5 is the proof: `from_task()` ran against a *remote* task and got far enough
to validate channels. The only thing missing anywhere is hardware.

### The usable API

`host/ni_grpc.py` wraps it up:

```python
import LDAQ
from ni_grpc import ldaq_ai_task

ai = ldaq_ai_task("rig", sample_rate=25600,
                  channels=[("Dev1/ai0", -10, 10), ("Dev1/ai1", -10, 10)])
acq = LDAQ.national_instruments.NIAcquisition(ai, acquisition_name="rig")
```

It also exposes `remote_task(name)`, `system()`, and `devices()` for plain
nidaqmx use. Session options are built **per task**, because of the session-name
rule above.

## Step 10 — USB passthrough (ready, untested)

`host/08-usb-passthrough.sh` is written but cannot be run until the chassis is
attached. It finds the NI device by vendor `0x3923`, extracts the product id, and
attaches a `<hostdev>` with `--live --config` so it survives reboots, using
`startupPolicy='optional'` so the VM still boots with the chassis unplugged.

Needs `usbutils` on the host (`lsusb` is not currently installed).

## Status: working, pending hardware

| Layer | Status |
|---|---|
| Host virt stack, ufw | done |
| VM, autostart, static IP, `ssh ni-daq` | done |
| Guest kernel 6.14.0-37 held, DKMS | done |
| NI-DAQmx 26.5.0, 32 modules built | done |
| gRPC server on 31763 | done |
| Arch client, driver reachable | done |
| LDAQ over gRPC | **verified, no patch needed** |
| USB passthrough | script ready, needs hardware |
| Real signal flow | needs hardware |

## Step 11 — USB passthrough, and the PID that moves

Chassis detected on the host: **`3923:74a5  National Instruments cDAQ 9174`**,
serial `213ADA3`. (`usbutils` is not installed; `host/08-usb-passthrough.sh` now
reads `/sys/bus/usb/devices/*/idVendor` instead of calling `lsusb`.)

First attach worked immediately and the device appeared in the guest. Then it
vanished, and a guest reboot and a full VM restart both failed to bring it back —
`startupPolicy='optional'` silently skips a device it cannot find, which hid the
reason. Attaching *without* that policy surfaced it:

```
error: internal error: Did not find matching USB device: vid:3923, pid:74a5, bus:0, device:0, port:
```

### The cause: the chassis changes its product ID

```
before: 3923:74a5  cDAQ 9174  serial 213ADA3
after : 3923:735b  cDAQ 9174  serial 213ADA3
```

Same chassis, same serial, **different PID**. The cDAQ-9174 enumerates as `74a5`
in its uninitialised state; once the NI driver in the guest initialises it, the
device resets and comes back as `735b`. The host re-enumerates it (new device
number too, 16 → 17), and the `<hostdev>` pinned to `74a5` no longer matches — so
the passthrough drops precisely because it succeeded.

Attaching `0x735b` brought it back, and the guest sees it.

**Consequence for daily use:** every physical replug or chassis power-cycle
restarts this cycle — the chassis comes back as `74a5`, gets initialised, becomes
`735b`, and the passthrough breaks at the changeover. Matching on vendor+product
alone cannot survive that. Options, best first:

1. A host **udev rule** on vendor `3923` that runs `virsh attach-device` on `add`.
   Handles both IDs and both directions automatically.
2. Two `<hostdev>` entries, one per PID, both `startupPolicy='optional'`. Covers
   VM start in either state but not a live changeover.
3. PCI passthrough of the whole xHCI controller, so all re-enumeration happens
   inside the guest. Most robust, but takes every other device on that controller
   with it — the dock and its peripherals are on the same bus here.

Not yet implemented; deferred until acquisition is proven.

### NI-DAQmx sees the chassis

With the device attached, all seven NI daemons active (`nipal nidevldu nidrum
nimxssvr nisds nisvcloc niroco`), `nidaqmxconfig --export` gives:

```ini
[DAQmx]
MajorVersion = 26
MinorVersion = 5

[DAQmxCDAQChassis cDAQ1]
ProductType = cDAQ-9174
DevSerialNum = 0x213ADA3
```

So the driver is talking to the chassis over the passed-through USB link, and it
has been assigned the name `cDAQ1`.

### But there are no modules

`System.remote(...).devices` from Arch returns `[]`, and the export lists **only**
the chassis — no `[DAQmxDevice ...]` sections. For CompactDAQ, the chassis itself
is not a DAQmx *device*; the C-series modules in its slots are. An empty list
means no modules are detected in the four slots.

This is no longer a plumbing question — the chain works, the chassis is
recognised by name and serial. Either the chassis is empty, or the modules are
not seating/enumerating.

### Modules found — the reconcile fixes it

The chassis had two modules all along; they could not enumerate because the
passthrough kept dropping mid-firmware-load. Doing a full **detach-all then
attach-current** reconcile (rather than trusting libvirt's "already attached")
let the guest hold the device through the `74a5 → 735b` transition, and:

```
[DAQmxCDAQChassis cDAQ1]   ProductType = cDAQ-9174
[DAQmxCDAQModule  cDAQ1Mod1] ProductType = NI 9263
[DAQmxCDAQModule  cDAQ1Mod2] ProductType = NI 9232 (BNC)

from Arch:  devices: ['cDAQ1', 'cDAQ1Mod1', 'cDAQ1Mod2']
```

Note a cosmetic leftover: the cycling registered the chassis twice, as `cDAQ1`
and `cDAQ2`, for one physical unit. Harmless but worth cleaning up in
`nidaqmxconfig` eventually.

**Bug found in `host/ni-daq-usb`:** it skipped re-attaching when libvirt already
claimed the matching product id. But libvirt keeps the hostdev in the live XML
after the device re-enumerates out from under QEMU, so that claim can be stale
with nothing behind it — and the stale claim blocks the re-attach. Fixed: always
detach every NI hostdev and attach whatever is physically present. Also added
`flock`, because udev fired the script twice concurrently and the two runs raced
(visible in the log as duplicated lines and a spurious detach error).

**Re-install after this fix:** `host/09-install-udev-rule.sh`

## Step 12 — ACQUISITION WORKS

Plain nidaqmx from Omarchy, 3 channels of the NI-9232 at 25.6 kHz:

```
read (3, 2560) in 252 ms (100 ms of signal)
  cDAQ1Mod2/ai0: mean +0.001080 V  rms 0.000109 V
  cDAQ1Mod2/ai1: mean +0.000916 V  rms 0.000111 V
  cDAQ1Mod2/ai2: mean +0.000116 V  rms 0.000111 V
```

~0.11 mV RMS on open IEPE inputs is the expected noise floor.

**LDAQ over gRPC**, `host/ldaq_acquire_test.py`:

```
AITask   : task_name 'ldaq_rig'
           devices ['cDAQ1', 'cDAQ1Mod2']  products ['cDAQ-9174', 'NI 9232 (BNC)']
NIAcquisition 'rig' -> run_acquisition(1.0)
  time: (25600,)   data: (25600, 2)
  25600 samples x 2 ch at 25600 Hz = 1.00 s
```

Exactly one second of data, no dropped samples.

### Three real bugs found along the way

**1. Finite vs continuous (mine).** `ldaq_ai_task` configured timing without
`sample_mode`, and nidaqmx defaults to FINITE. LDAQ streams by reading repeatedly
from a running task, so every read after the first `samps_per_chan` failed with
`-200278` "read a sample beyond the final sample acquired". Fixed by passing
`AcquisitionType.CONTINUOUS`.

**2. Orphaned gRPC sessions hold the hardware.** A script that dies without
closing its task leaves the session alive on the server. The next run either
attaches to it silently (and then fails `-200489` "channel already in the task")
or cannot reserve the device at all (`-50103` "resource is reserved"). This cost
real debugging time — two separate symptoms that looked like gRPC limitations
were purely this. `ldaq_ai_task` now reuses channels that already exist, and
`ni_grpc.restart_server()` clears every session when things get wedged.

**3. `read(-1)` on an empty buffer behaves differently over gRPC.** This is the
one that genuinely needs an upstream fix.

`acquire()` with no argument reads `number_of_samples_per_channel=-1`
(READ_ALL_AVAILABLE). Locally that returns an empty array when the buffer is
empty. Over gRPC it raises `DaqError -52005`. Measured in a tight loop:

```
before:  reads OK=59 (0-sample returns: 0)  errors=1  {-52005: 1}
after :  acquire() x120: OK=120 (empty=1)   errors=0
```

Roughly one failure per 60 reads — an acquisition loop polls faster than the
hardware fills the buffer, so this is not an edge case; it makes remote
acquisition unusable. `ni_grpc._patch_acquire_for_grpc()` monkey-patches
`AITask.acquire` at runtime so it works today. The proper fix for upstream is in
`patches/nidaqwrapper-grpc-empty-read.patch` — a try/except around the one read,
returning the empty array the caller already expects, with no extra RPC on the
common path.

## Final state: fully working

| Layer | Status |
|---|---|
| Host virt stack, ufw | done |
| VM, autostart, static IP, `ssh ni-daq` | done |
| Guest kernel 6.14.0-37 held, DKMS | done |
| NI-DAQmx 26.5.0, 32 modules | done |
| gRPC server on 31763 | done |
| USB passthrough across re-enumeration | done (udev reconcile) |
| Device enumeration: cDAQ1, Mod1, Mod2 | done |
| Plain nidaqmx acquisition from Arch | done |
| **LDAQ acquisition from Arch** | **done** |

### Daily use

```python
import LDAQ
import sys; sys.path.insert(0, "~/Work/ni-daq-vm/host")
from ni_grpc import ldaq_ai_task

ai = ldaq_ai_task("rig", sample_rate=25600,
                  channels=[("cDAQ1Mod2/ai0", -5, 5), ("cDAQ1Mod2/ai1", -5, 5)])
acq = LDAQ.national_instruments.NIAcquisition(ai, acquisition_name="rig")
```

If anything gets wedged after a crashed script: `ni_grpc.restart_server()`.
