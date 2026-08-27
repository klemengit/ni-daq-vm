"""Prove the LDAQ layer works against a remote gRPC task, without hardware.

Without a device we cannot add channels, so full acquisition cannot be tested.
What we can prove is that every LDAQ/nidaqwrapper code path that matters runs
against a *remote* task and never reaches for a local driver.
"""

import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import nidaqmx
from nidaqmx.errors import DaqError

import ni_grpc


def check(label: str, fn):
    try:
        return label, "OK", fn()
    except Exception as exc:
        return label, type(exc).__name__, exc


def main() -> None:
    print("1. nidaqmx imports on Arch with no NI driver")
    print(f"   nidaqmx {nidaqmx.__version__}")

    print("\n2. nidaqwrapper's driver guard passes (it checks the package, not a driver)")
    from nidaqwrapper.utils import _require_nidaqmx

    _require_nidaqmx()
    print("   _require_nidaqmx() OK")

    print("\n3. remote system is reachable")
    print(f"   driver  : NI-DAQmx {ni_grpc.system().driver_version}")
    print(f"   devices : {ni_grpc.devices() or 'none attached'}")

    print("\n4. a remote task can be created and inspected over gRPC")
    with ni_grpc.remote_task("ldaq_probe") as task:
        print(f"   task name    : {task.name}")
        print(f"   ai_channels  : {len(task.ai_channels)}")

        print("\n5. AITask.from_task() operates on the REMOTE task")
        from nidaqwrapper import AITask

        try:
            AITask.from_task(task)
            print("   wrapped (unexpected without channels)")
        except ValueError as exc:
            print(f"   ValueError: {exc}")
            print("   -> reached the channel check, so it read ai_channels over gRPC")
            print("   -> __init__ (and its System.local() call) was correctly skipped")

        print("\n6. only hardware is missing")
        try:
            task.ai_channels.add_ai_voltage_chan("Dev1/ai0")
            print("   channel added - hardware present!")
        except DaqError as exc:
            verdict = "expected: no device attached" if exc.error_code == -200220 else "UNEXPECTED"
            print(f"   DaqError {exc.error_code} ({verdict})")

    print("\nConclusion: the LDAQ path needs no patch. Attach hardware and use")
    print("ni_grpc.ldaq_ai_task(...) -> LDAQ.national_instruments.NIAcquisition(...)")


if __name__ == "__main__":
    main()
