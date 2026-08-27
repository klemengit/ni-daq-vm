"""Acquire real samples from the NI 9232 over gRPC, from Omarchy."""

import sys, time
sys.path.insert(0, __file__.rsplit("/", 1)[0])

import numpy as np
import ni_grpc

RATE = 25600
N = 2560
CHANS = ["cDAQ1Mod2/ai0", "cDAQ1Mod2/ai1", "cDAQ1Mod2/ai2"]

print(f"devices: {ni_grpc.devices()}")

with ni_grpc.remote_task("acq") as task:
    for ch in CHANS:
        task.ai_channels.add_ai_voltage_chan(ch, min_val=-5.0, max_val=5.0)
    task.timing.cfg_samp_clk_timing(rate=RATE, samps_per_chan=N)

    print(f"\nconfigured {len(CHANS)} ch @ {task.timing.samp_clk_rate:.1f} Hz")

    t0 = time.perf_counter()
    data = np.asarray(task.read(number_of_samples_per_channel=N, timeout=10.0))
    dt = time.perf_counter() - t0

    print(f"read {data.shape} in {dt*1000:.0f} ms "
          f"({N/RATE*1000:.0f} ms of signal, so {dt/(N/RATE):.2f}x realtime)")
    for ch, row in zip(CHANS, np.atleast_2d(data)):
        print(f"  {ch}: mean {row.mean():+.6f} V  rms {row.std():.6f} V  "
              f"min {row.min():+.4f}  max {row.max():+.4f}")
