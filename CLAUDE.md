# ni-daq-vm

NI-DAQmx runs in a headless Ubuntu 24.04 VM that owns the NI USB chassis; host
Python talks to it over NI's gRPC device server (`:31763`). No NI driver on the
host, ever. Full story and gotchas: `README.md` ("Things that will bite you").

## Layout
- `setup.sh` — one-time install orchestrator.
- `host/` — Arch side (libvirt, udev rule, the `ni-daq` command).
- `guest/`, `cloud-init/` — Ubuntu side (kernel pin, NI-DAQmx, gRPC server).
- `client/ni_grpc.py` — client API. Other projects (e.g. sigma_method's
  `daq_paths.py`) put `client/` on `sys.path` and call `patch_legacy_local()`
  and `patch_legacy_acquire()` so unmodified LDAQ code hits the VM.

## Run
- `ni-daq up` / `ni-daq down` / `ni-daq status`.
- Stuck session (`-50103`, `-200489`): `ni-daq release`.

## Gotchas
- Any `nidaqmx` object created without `grpc_options` falls back to the local
  driver → `DaqNotFoundError`. `patch_legacy_local()` covers `System.local()`,
  `Task` and `Scale.create_*`; add new classes there if the error reappears.
- Upstream repo is `klemengit/ni-daq-vm` (shared with Klemen Zaletelj).
