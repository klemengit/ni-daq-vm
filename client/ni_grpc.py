"""Drive NI hardware on the ni-daq VM from Omarchy.

No NI driver is installed on this machine. Everything goes over gRPC to the
NI gRPC Device Server running in the ni-daq VM, which owns the USB hardware.

Usage with LDAQ::

    import LDAQ
    from ni_grpc import ldaq_ai_task

    ai = ldaq_ai_task("rig", sample_rate=25600,
                      channels=[("Dev1/ai0", -10, 10), ("Dev1/ai1", -10, 10)])
    acq = LDAQ.national_instruments.NIAcquisition(ai, acquisition_name="rig")
    ...

Usage with plain nidaqmx::

    from ni_grpc import remote_task

    with remote_task("quick") as task:
        task.ai_channels.add_ai_voltage_chan("Dev1/ai0")
        data = task.read(number_of_samples_per_channel=1000)
"""

from __future__ import annotations

import grpc
import numpy as np
import nidaqmx
from nidaqmx.constants import AcquisitionType
from nidaqmx.errors import DaqError

SERVER = "192.168.122.50:31763"

_channel: grpc.Channel | None = None


def channel(server: str = SERVER) -> grpc.Channel:
    """Return the process-wide gRPC channel, creating it on first use."""
    global _channel
    if _channel is None:
        _channel = grpc.insecure_channel(server)
    return _channel


def options(session_name: str = "", server: str = SERVER) -> nidaqmx.GrpcSessionOptions:
    """Build session options for one task.

    For NI-DAQmx the gRPC session name *is* the task name -- the server rejects
    any ``session_name`` that does not match the task being opened. Pass ``""``
    for system-level calls. This is why options are built per task rather than
    shared.
    """
    return nidaqmx.GrpcSessionOptions(
        grpc_channel=channel(server),
        session_name=session_name,
        initialization_behavior=nidaqmx.SessionInitializationBehavior.AUTO,
    )


def system(server: str = SERVER):
    """The remote NI-DAQmx system: driver version, device list, and so on."""
    return nidaqmx.system.System.remote(options("", server))


def devices(server: str = SERVER) -> list[str]:
    """Names of the DAQ devices attached to the VM."""
    return list(system(server).devices.device_names)


def remote_task(name: str, server: str = SERVER) -> nidaqmx.Task:
    """A nidaqmx Task that lives on the VM. Use as a context manager."""
    return nidaqmx.Task(new_task_name=name, grpc_options=options(name, server))


def _patch_acquire_for_grpc() -> None:
    """Make ``AITask.acquire()`` tolerate an empty buffer over gRPC.

    ``acquire()`` with no argument reads with ``number_of_samples_per_channel=-1``
    (READ_ALL_AVAILABLE). Against a local driver that returns an empty array when
    the buffer happens to be empty. Over gRPC it instead raises DaqError
    **-52005** ("The parameter received by the function is not valid").

    An acquisition loop polls faster than the hardware fills the buffer, so this
    is not an edge case -- measured at roughly 1 failure per 60 tight reads. This
    wrapper turns that one error back into the empty array the caller expects,
    and leaves every other error alone.

    Costs nothing on the common path: no extra RPC, just an except clause.

    Upstream fix: https://github.com/ladisk/nidaqwrapper/pull/9, merged to main
    2026-08-27 but not yet in a release (PyPI is still 0.2.0). This function
    detects the fix and does nothing once it is installed, so upgrading
    nidaqwrapper is all that is needed -- no code change here.
    """
    import inspect

    from nidaqwrapper.ai_task import AITask

    if getattr(AITask.acquire, "_grpc_patched", False):
        return

    # Self-disable once the installed nidaqwrapper carries the upstream fix.
    # Checking the source beats checking a version number: it stays correct
    # whatever the release is called, and works for a git install too.
    try:
        if "-52005" in inspect.getsource(AITask.acquire):
            return
    except (OSError, TypeError):
        pass  # no source available (zipimport, compiled): patching is harmless

    original = AITask.acquire

    def acquire(self, n_samples=None):
        try:
            return original(self, n_samples)
        except DaqError as exc:
            if n_samples is None and exc.error_code == -52005:
                # Mirror exactly what a local driver returns for an empty
                # buffer: np.array([]) reshaped to (0, 1).
                return np.empty((0, 1))
            raise

    acquire._grpc_patched = True
    acquire.__doc__ = original.__doc__
    AITask.acquire = acquire


def ldaq_ai_task(
    task_name: str,
    sample_rate: float,
    channels: list[tuple[str, float, float]],
    samples_per_channel: int | None = None,
    server: str = SERVER,
):
    """Build an ``nidaqwrapper.AITask`` backed by a remote gRPC task.

    LDAQ's own ``AITask(...)`` constructor calls ``nidaqmx.system.System.local()``
    and so cannot work here. ``AITask.from_task()`` bypasses ``__init__`` entirely
    (via ``object.__new__``) and reads everything from the live task, so it works
    unchanged against a remote one. No patch to LDAQ or nidaqwrapper is needed.

    Parameters
    ----------
    task_name : str
        DAQmx task name. Also used as the gRPC session name.
    sample_rate : float
        Sample clock rate in Hz.
    channels : list of (str, float, float)
        ``(physical_channel, min_val, max_val)`` per channel.
    samples_per_channel : int, optional
        Buffer size. Defaults to one second of data.

    Returns
    -------
    nidaqwrapper.AITask
        Ready to hand to ``LDAQ.national_instruments.NIAcquisition``.
    """
    from nidaqwrapper import AITask

    _patch_acquire_for_grpc()

    raw = remote_task(task_name, server)
    try:
        # A crashed run leaves its session alive on the server, and
        # initialization_behavior=AUTO silently attaches to it -- channels and
        # all. Re-adding them then fails with -200489. Reuse what is already
        # there instead, which makes this function safe to re-run.
        existing = {c.name.split("/")[-1] for c in raw.ai_channels}
        for physical_channel, min_val, max_val in channels:
            if physical_channel.split("/")[-1] in existing:
                continue
            raw.ai_channels.add_ai_voltage_chan(
                physical_channel, min_val=min_val, max_val=max_val
            )
        raw.timing.cfg_samp_clk_timing(
            rate=sample_rate,
            # LDAQ streams: it reads repeatedly from a running task, so the
            # acquisition must be CONTINUOUS. The nidaqmx default is FINITE,
            # which stops after samps_per_chan and then fails every later read
            # with -200278 "read a sample beyond the final sample acquired".
            sample_mode=AcquisitionType.CONTINUOUS,
            samps_per_chan=samples_per_channel or int(sample_rate),
        )
    except Exception:
        raw.close()
        raise

    return AITask.from_task(raw, take_ownership=True)


def release(server: str = SERVER, timeout: float = 20.0) -> None:
    """Drop every session the gRPC server holds.

    A script that dies without closing its task leaves the session alive on
    the server, still holding the hardware. The next run then fails with
    -50103 ("resource is reserved") or -200489 ("channel already in task"),
    or silently attaches to the stale session. There is no client-side call
    that reaches a session whose client is already gone, so the server is
    asked to reset itself.

    Uses the device server's own ``ResetServer`` call, so it needs no ssh, no
    sudo and no systemd -- unlike :func:`restart_server`, it works from any
    machine that can reach the port, Windows included.

    Blunt by design: it drops sessions belonging to every client, not only
    this one. Fine while a single station owns the server.

    Raises
    ------
    RuntimeError
        If the server declines the reset.
    grpc.RpcError
        If the server cannot be reached.
    """
    import session_pb2
    import session_pb2_grpc

    stub = session_pb2_grpc.SessionUtilitiesStub(channel(server))
    reply = stub.ResetServer(session_pb2.ResetServerRequest(), timeout=timeout)
    if not reply.is_server_reset:
        raise RuntimeError("the gRPC server declined the reset")

    # Every session opened on the old channel is gone; drop it so the next
    # call opens a fresh one rather than reusing sessions that no longer exist.
    global _channel
    _channel = None


def restart_server(host: str = "ni-daq") -> None:
    """Restart the gRPC service over ssh.

    The heavy-handed alternative to :func:`release`: it replaces the process
    rather than asking it to reset, so it is what to reach for when the
    server itself is wedged and no longer answers RPCs. Needs ssh and sudo on
    the guest. Prefer :func:`release` otherwise.
    """
    import subprocess

    subprocess.run(
        ["ssh", host, "sudo systemctl restart ni-grpc-device"],
        check=True, timeout=60,
    )
    global _channel
    _channel = None
