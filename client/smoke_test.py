"""Verify the Arch -> gRPC -> Ubuntu -> NI-DAQmx chain.

Run from Omarchy. No NI driver is installed on this machine; everything goes
over gRPC to the ni-daq VM.

What the outcomes mean:
  devices listed                -> whole chain works, hardware present
  DaqError -200220              -> whole chain works, no hardware attached (expected pre-rack)
  "driver not installed"        -> transport fine, NI-DAQmx missing in the guest
  gRPC UNAVAILABLE / timeout    -> transport broken (VM down, port, firewall)
"""

import grpc
import nidaqmx
from nidaqmx.errors import DaqError

SERVER = "192.168.122.50:31763"


def main() -> None:
    print(f"connecting to {SERVER}")
    channel = grpc.insecure_channel(SERVER)
    try:
        grpc.channel_ready_future(channel).result(timeout=5)
        print("  transport : OK (gRPC channel ready)")
    except grpc.FutureTimeoutError:
        print("  transport : FAILED - server unreachable")
        return

    def opts_for(session_name: str = "") -> nidaqmx.GrpcSessionOptions:
        # For NI-DAQmx the gRPC session name IS the task name -- the server
        # rejects any session_name that does not match the task it is opening.
        # Use "" for system-level calls.
        return nidaqmx.GrpcSessionOptions(
            grpc_channel=channel,
            session_name=session_name,
            initialization_behavior=nidaqmx.SessionInitializationBehavior.AUTO,
        )

    opts = opts_for("")

    system = nidaqmx.system.System.remote(opts)
    try:
        print(f"  driver    : NI-DAQmx {system.driver_version}")
        names = list(system.devices.device_names)
        print(f"  devices   : {names or 'none attached'}")
    except DaqError as exc:
        print(f"  driver    : DaqError {exc.error_code}: {exc}")
    except Exception as exc:
        print(f"  driver    : {type(exc).__name__}: {exc}")

    # Pick a real AI channel when hardware is present; fall back to a name that
    # cannot exist, so the no-hardware case still produces the -200220 signal.
    chan = "Dev1/ai0"
    try:
        for name in system.devices.device_names:
            ai = list(system.devices[name].ai_physical_chans.channel_names)
            if ai:
                chan = ai[0]
                break
    except Exception:
        pass

    print(f"\ntask creation on {chan} (expect -200220 if no hardware):")
    try:
        with nidaqmx.Task(new_task_name="smoke_task",
                          grpc_options=opts_for("smoke_task")) as task:
            task.ai_channels.add_ai_voltage_chan(chan)
            print(f"  channel added on {chan} - hardware is live")
    except DaqError as exc:
        verdict = "chain OK, no hardware" if exc.error_code == -200220 else "see error"
        print(f"  DaqError {exc.error_code} ({verdict})")
        print(f"    {str(exc).splitlines()[0]}")
    except Exception as exc:
        print(f"  {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
