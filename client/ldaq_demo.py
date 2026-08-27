"""Acquire through LDAQ over gRPC and plot it. The 'does it really work' script.

Run it with the NI chassis plugged in and the VM up::

    .venv/bin/python client/ldaq_demo.py                 # 2 s, 2 channels
    .venv/bin/python client/ldaq_demo.py --seconds 5     # longer record
    .venv/bin/python client/ldaq_demo.py --channels 3    # all NI-9232 inputs

Nothing needs to be connected to the BNC inputs -- open IEPE inputs sit at a
few tenths of a mV and you will see the noise floor.  Tap a connected
accelerometer, or touch the centre pin of an input, and it will show up in the
trace.

The real proof is the timing line: LDAQ asks for N seconds and the remote
driver has to deliver exactly ``N * sample_rate`` samples.  If the gRPC hop
were dropping or duplicating data, that number would drift.
"""

import argparse
import pathlib
import subprocess
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import numpy as np

import LDAQ
from ni_grpc import ldaq_ai_task

DEVICE = "cDAQ1Mod2"  # NI-9232, the analog input module
PLOT = pathlib.Path(__file__).resolve().parent.parent / "ldaq_demo.png"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--seconds", type=float, default=2.0)
    p.add_argument("--rate", type=float, default=25600)
    p.add_argument("--channels", type=int, default=2)
    p.add_argument("--no-plot", action="store_true")
    args = p.parse_args()

    channels = [(f"{DEVICE}/ai{i}", -5, 5) for i in range(args.channels)]

    ai = ldaq_ai_task("ldaq_demo", sample_rate=args.rate, channels=channels)
    print(f"devices    : {ai.device_list}")
    print(f"modules    : {ai.device_product_type}")
    print(f"acquiring  : {args.channels} ch x {args.seconds} s "
          f"@ {args.rate:.0f} Hz ...")

    acq = LDAQ.national_instruments.NIAcquisition(ai, acquisition_name="demo")
    acq.run_acquisition(args.seconds)
    data = acq.get_measurement_dict()["data"]
    ai.clear_task()

    expected = int(round(args.seconds * args.rate))
    drift = data.shape[0] - expected
    verdict = ("OK -- no samples lost over gRPC" if drift == 0
               else f"MISMATCH -- off by {drift:+d} samples")
    print(f"\nsamples    : {data.shape[0]} "
          f"({data.shape[0] / args.rate:.4f} s, expected {expected})")
    print(f"timing     : {verdict}")

    print()
    for i in range(data.shape[1]):
        col = data[:, i]
        print(f"  ai{i}: mean {1e3 * col.mean():+8.3f} mV   "
              f"rms {1e3 * col.std():7.3f} mV   "
              f"peak {1e3 * np.abs(col).max():8.3f} mV")

    if not args.no_plot:
        plot(data, args.rate)
        print(f"\nplot       : {PLOT}")
        subprocess.Popen(["xdg-open", str(PLOT)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return 0 if drift == 0 else 1


def plot(data: np.ndarray, rate: float) -> None:
    """Save a time trace and an amplitude spectrum for every channel."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n, n_ch = data.shape
    t = np.arange(n) / rate
    freq = np.fft.rfftfreq(n, 1 / rate)

    fig, axes = plt.subplots(n_ch, 2, figsize=(11, 2.6 * n_ch), squeeze=False)
    for i in range(n_ch):
        col = data[:, i]
        axes[i][0].plot(t, 1e3 * col, lw=0.5)
        axes[i][0].set_ylabel(f"ai{i}  [mV]")
        axes[i][0].set_xlim(0, t[-1])

        # Single-sided amplitude spectrum, Hann-windowed and scaled so a pure
        # tone reads its actual amplitude rather than a bin-dependent value.
        win = np.hanning(n)
        amp = 2 * np.abs(np.fft.rfft(col * win)) / win.sum()
        axes[i][1].semilogy(freq, 1e3 * np.maximum(amp, 1e-12), lw=0.5)
        axes[i][1].set_xlim(0, rate / 2)
        axes[i][1].set_ylabel("[mV]")

    axes[-1][0].set_xlabel("time [s]")
    axes[-1][1].set_xlabel("frequency [Hz]")
    axes[0][0].set_title("time trace")
    axes[0][1].set_title("amplitude spectrum")
    fig.suptitle(f"LDAQ over gRPC -- {n_ch} ch, {n} samples @ {rate:.0f} Hz")
    fig.tight_layout()
    fig.savefig(PLOT, dpi=110)


if __name__ == "__main__":
    raise SystemExit(main())
