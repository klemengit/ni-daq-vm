"""Live LDAQ acquisition over gRPC, with LDAQ's own visualization.

The 'does it really work' script.  Opens LDAQ's plot window and streams the
NI 9232 into it in real time -- time trace on top, spectrum below -- from a
machine with no NI software installed at all.

    .venv/bin/python demo/ldaq_demo.py                  # 10 s, 2 channels
    .venv/bin/python demo/ldaq_demo.py --seconds 30     # longer run
    .venv/bin/python demo/ldaq_demo.py --channels 3     # all NI 9232 inputs
    .venv/bin/python demo/ldaq_demo.py --no-vis         # headless, stats only

Nothing needs to be connected: open IEPE inputs sit at ~0.1 mV RMS, so you
will see the noise floor.  Tap a connected accelerometer, or touch an input's
centre pin, and it shows up live -- which is the point.  Watching the trace
respond to something you do with your hand is a better proof that the whole
chain works than any number this script can print.

The number that matters is still printed at the end.  LDAQ asks for N seconds
and the remote driver has to return exactly ``N * sample_rate`` samples; a
dropped or duplicated read anywhere in the gRPC hop would show up as drift.
The script exits non-zero if it does.
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "client"))

import numpy as np

import LDAQ
from ni_grpc import ldaq_ai_task

DEVICE = "cDAQ1Mod2"  # NI 9232, the analog input module
SOURCE = "rig"        # LDAQ's name for this acquisition source


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--seconds", type=float, default=10.0)
    p.add_argument("--rate", type=float, default=25600)
    p.add_argument("--channels", type=int, default=2)
    p.add_argument("--span", type=float, default=0.1,
                   help="seconds of signal visible in the live trace")
    p.add_argument("--fft-span", type=float, default=1.0,
                   help="seconds transformed for the live spectrum")
    p.add_argument("--no-vis", action="store_true",
                   help="run headless -- no plot window")
    args = p.parse_args()

    channels = [(f"{DEVICE}/ai{i}", -5, 5) for i in range(args.channels)]

    ai = ldaq_ai_task("ldaq_demo", sample_rate=args.rate, channels=channels)
    print(f"devices    : {ai.device_list}")
    print(f"modules    : {ai.device_product_type}")
    print(f"acquiring  : {args.channels} ch x {args.seconds:g} s "
          f"@ {args.rate:.0f} Hz")

    acq = LDAQ.national_instruments.NIAcquisition(ai, acquisition_name=SOURCE)

    if args.no_vis:
        # Headless goes straight to the acquisition object.  LDAQ's Core
        # drives the run from its visualization event loop, so with no window
        # and no hotkey listener there is nothing left to end the run and it
        # blocks forever.
        acq.run_acquisition(args.seconds)
        data = acq.get_measurement_dict()["data"]
    else:
        # hotkeys=False: LDAQ's global-hotkey listener is the `keyboard`
        # package, which reads /dev/input directly and demands root on Linux.
        core = LDAQ.Core([acq], visualization=build_visualization(args))
        # autostart: without it LDAQ opens the window and waits for a
        # click on Start, and the measurement clock only begins then.
        core.run(args.seconds, hotkeys=False, autostart=True)
        data = core.get_measurement_dict()[SOURCE]["data"]

    ai.clear_task()
    return report(data, args)


def build_visualization(args) -> "LDAQ.Visualization":
    """Time trace over a live amplitude spectrum, one line per channel."""
    chans = list(range(args.channels))
    nyquist = args.rate / 2

    vis = LDAQ.Visualization(refresh_rate=50)

    vis.add_lines(position=(0, 0), source=SOURCE, channels=chans)
    vis.config_subplot((0, 0), t_span=args.span,
                       title=f"time trace [V] -- last {args.span:g} s")

    vis.add_lines(position=(1, 0), source=SOURCE, channels=chans,
                  function="fft")
    # t_span is mandatory here, not cosmetic.  LDAQ sizes its ring buffer as
    # max(t_span) * sample_rate over every subplot, and when t_span is
    # omitted it derives one from xlim -- which on a spectrum is in Hz.  At
    # 25.6 kHz that would ask for 12800 s of buffer, i.e. several GB, and the
    # process is OOM-killed before the window ever appears.
    vis.config_subplot((1, 0), xlim=(0, nyquist), t_span=args.fft_span,
                       axis_style="semilogy",
                       title=f"amplitude spectrum [V] -- {args.fft_span:g} s "
                             f"window, {1 / args.fft_span:g} Hz resolution")
    return vis


def report(data: np.ndarray, args) -> int:
    """Print per-channel stats and the sample-count check."""
    expected = int(round(args.seconds * args.rate))
    drift = data.shape[0] - expected

    print()
    for i in range(data.shape[1]):
        col = data[:, i]
        print(f"  ai{i}: mean {1e3 * col.mean():+8.3f} mV   "
              f"rms {1e3 * col.std():7.3f} mV   "
              f"peak {1e3 * np.abs(col).max():8.3f} mV")

    print(f"\nsamples    : {data.shape[0]} "
          f"({data.shape[0] / args.rate:.4f} s, expected {expected})")

    if drift == 0:
        print("timing     : OK -- nothing lost over gRPC")
        return 0
    if drift < 0:
        # LDAQ's trigger keeps reading until it has the full record, so a
        # short one means the run was cut off -- closing the window early --
        # not that samples went missing in transit.
        print("timing     : stopped early -- window closed before the run "
              "finished")
        return 0
    print(f"timing     : MISMATCH -- {drift:+d} samples more than asked for")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
