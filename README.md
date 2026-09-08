# ni-daq-vm

Run National Instruments DAQ hardware from a Linux distribution NI does not
support, by giving the driver its own kernel.

NI-DAQmx installs DKMS kernel modules and supports only RHEL, openSUSE and
Ubuntu LTS, on a narrow band of kernel versions. That rules out Arch, and it
means a bare-metal install holds your machine's kernel hostage to NI's release
schedule. This puts NI-DAQmx in a headless Ubuntu VM that owns the USB hardware,
and exposes it over NI's own gRPC device server. Your Python stays on the host,
with no NI software installed there at all.

```
Arch host                                Ubuntu 24.04 VM (headless)
---------                                --------------------------
Python + LDAQ                            ni_grpc_device_server  :31763
nidaqmx[grpc]  ──── gRPC over ─────────► NI-DAQmx 26.5.0
(no NI driver)      libvirt NAT          nikal / nipalk kernel modules
                                                  │ USB passthrough
                                                  ▼
                                             NI USB chassis
```

Built and verified on Omarchy (Arch) with a cDAQ-9174 carrying an NI-9263 and an
NI-9232, acquiring through LDAQ at 25.6 kHz.

## Install

```bash
git clone <this repo> && cd ni-daq-vm
./setup.sh
```

Roughly 20-30 minutes, mostly downloads. Stages are idempotent and can be run
individually — `./setup.sh --list`, then e.g. `./setup.sh guest verify`.

The guest account is named after your host username; set `GUEST_USER=...` before
`./setup.sh` if you want a different one.

Requires: an Arch host with KVM, `uv`, and sudo.

## Use

The VM does not autostart -- it holds ~2 GB of RAM and is only useful with the
chassis plugged in. Bring it up when you want to measure:

```bash
ni-daq up        # when you want to measure
ni-daq down      # when you are done
```

| Command | What it does | Needs root? |
|---|---|---|
| `ni-daq up` | Starts the domain, waits for the gRPC server, waits for the driver to report devices. Falls back to the privileged reconcile only if libvirt did not attach the chassis itself. | no, on a cold start |
| `ni-daq down` | Clean `virsh shutdown`, with a `destroy` only after 60 s. | no |
| `ni-daq status` | Domain state, QEMU pid and resident RAM, gRPC reachability, visible devices, whether the chassis is on host USB, and a warning if the installed `ni-daq-usb` has drifted from the repo. This is the default — a bare `ni-daq` runs it. | no |
| `ni-daq attach` | Reconciles the domain against whatever NI device is physically plugged in. For a chassis connected after the VM was already up. | sudo, on exactly `ni-daq-usb attach` |
| `ni-daq release` | Asks the server to drop every gRPC session, then proves the hardware is actually free by reserving a channel — listing devices cannot tell a reserved chassis from a free one. | no |
| `ni-daq reset` | Restarts `ni-grpc-device` in the guest. The heavier fallback for when the server stops answering; drops every open session. | sudo in the guest, over ssh |
| `ni-daq ssh [cmd]` | Shell into the guest, or run one command there. | no |
| `ni-daq log [n]` | Tails the last `n` (default 30) lines of `/var/log/ni-daq-usb.log`, the USB attach log. | no |
| `ni-daq help` | Prints the command list from the script's own header. | no |

Then, from Python:

```python
import sys; sys.path.insert(0, "client")   # or this repo's client/ by absolute path
import LDAQ
from ni_grpc import ldaq_ai_task

ai = ldaq_ai_task("rig", sample_rate=25600,
                  channels=[("cDAQ1Mod2/ai0", -5, 5), ("cDAQ1Mod2/ai1", -5, 5)])
acq = LDAQ.national_instruments.NIAcquisition(ai, acquisition_name="rig")
acq.run_acquisition(1.0)
data = acq.get_measurement_dict()["data"]
```

Plain nidaqmx works too:

```python
from ni_grpc import remote_task, devices
print(devices())
with remote_task("quick") as task:
    task.ai_channels.add_ai_voltage_chan("cDAQ1Mod2/ai0")
    task.timing.cfg_samp_clk_timing(rate=25600, samps_per_chan=2560)
    data = task.read(number_of_samples_per_channel=2560)
```

`ni_grpc.release()` clears orphaned sessions after a crashed script, over the
server's own reset call -- no ssh or sudo, so it works from anywhere that can
reach the port.

### See it work

```bash
.venv/bin/python demo/ldaq_demo.py               # live plot, 10 s, 2 channels
.venv/bin/python demo/ldaq_demo.py --no-vis      # headless, stats only
```

Opens LDAQ's own plot window -- live time trace over a live spectrum -- fed
from the chassis through the VM. Touch an input's centre pin and you will see
it. It prints the sample count at the end and exits non-zero if the record is
short, so it doubles as a smoke test after a reboot or a replug.

## Layout

| Path | What |
|---|---|
| `setup.sh` | orchestrator; run this |
| `host/` | Arch-side: libvirt, VM creation, ufw, USB udev rule, the `ni-daq` command |
| `guest/` | Ubuntu-side: kernel pin, NI-DAQmx, gRPC server |
| `cloud-init/` | first-boot identity and network config |
| `client/` | `ni_grpc.py`, the client-side API, and the smoke test `setup.sh` runs |
| `demo/` | runnable end-to-end demos, including the live-plot one |
| `SETUP-LOG.md` | full build log, including the dead ends |

## Things that will bite you

**Pin the guest kernel.** NI publishes a *minimum* kernel per distro (24.04 →
6.11) but breaks on newer ones, and Ubuntu's HWE metapackage has already moved to
7.0. The guest runs 6.14.0-37 with `apt-mark hold` on every kernel metapackage
and unattended-upgrades disabled. Verified: all 32 NI DKMS modules build against
it with NI-DAQmx 2026 Q3, including `nipalk`, which fails on 6.14 with older
releases.

**The `<hostdev>` entries in the domain XML are load-bearing.** There is one
per NI product id, each with `startupPolicy='optional'`, so libvirt attaches
whatever is plugged in as the domain starts -- which is why a cold `ni-daq up`
needs no privileges at all. They look like leftovers from the abandoned
pin-the-product-id approach, and deleting them costs you the unprivileged
attach. `startupPolicy` applies only at domain start, so a chassis plugged in
mid-session still needs `ni-daq attach`.

**`virsh autostart` alone does not survive a reboot.** Arch enables only
`libvirtd.socket`, not `libvirtd.service`, so libvirt starts on the first
connection rather than at boot -- and guest autostart is something the daemon
does when *it* starts. Marking the domain autostart therefore does nothing
until you happen to run a `virsh` command. This repo does not rely on it: the
domain is explicitly *not* autostarted and `ni-daq up` starts it on demand,
which socket-activates libvirt on the way. If you ever do want it at boot, it
needs `systemctl enable libvirtd.service`, not just the autostart flag.

**ufw silently eats the guest's DHCP.** Omarchy enables ufw with default-deny
incoming. The guest's DHCP broadcast to UDP/67 arrives on `virbr0` and is dropped
before dnsmasq sees it, so the VM boots but never gets an address, with
thoroughly misleading symptoms. `setup.sh ufw` fixes it, before first boot.

**The chassis changes its USB product id.** A cDAQ-9174 enumerates as
`3923:74a5`, and once the guest's NI driver loads its firmware it resets and
comes back as `3923:735b`. A `<hostdev>` pinned to one id loses the device at
exactly the moment it starts working — and the modules never enumerate. Solved by
a udev rule on vendor `3923` that reconciles the domain against whatever is
physically present. Never trust libvirt's "already attached": it keeps stale
hostdevs in the live XML after re-enumeration, and the stale claim blocks the
re-attach.

**Orphaned gRPC sessions hold the hardware.** A script that dies without closing
its task leaves the session alive on the server. The next run fails with `-50103`
(resource reserved) or `-200489` (channel already in task). Run `ni-daq release`,
or call `ni_grpc.release()`.

Note what "holds" means: only a task that was *reserved or started* claims the
hardware. A session that merely added channels blocks nothing, so two tasks can
name the same input without complaint. `ni-daq release` checks by reserving,
which is why it can tell a free chassis from a held one where listing devices
cannot -- a reserved device still enumerates.

**No simulated devices on Linux.** Those are a NI MAX feature and MAX is
Windows-only. Without hardware, the correct "it all works" signal is DAQmx
`-200220` (invalid device identifier) arriving over gRPC — that means every layer
is fine and only the hardware is absent.

**`read(-1)` differs over gRPC.** READ_ALL_AVAILABLE on an empty buffer returns
an empty array locally but raises `DaqError -52005` over gRPC, about once every
sixty reads in an acquisition loop. `ni_grpc` patches `AITask.acquire` at runtime;
fixed upstream in [ladisk/nidaqwrapper#9](https://github.com/ladisk/nidaqwrapper/pull/9) (merged, not yet released). `ni_grpc` patches `AITask.acquire` at runtime and self-disables once the fix is installed.

## Performance

Each DAQmx call is an RPC over the host-only NAT, so latency is sub-millisecond
but not free. Hardware-timed buffered acquisition is unaffected — read in blocks.
A software-timed per-sample control loop is the wrong shape for this setup.
