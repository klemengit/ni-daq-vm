"""Acquire through LDAQ over gRPC, from Omarchy. The real end-to-end test."""

import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "client"))

import numpy as np
import LDAQ
from ni_grpc import ldaq_ai_task

RATE = 25600

ai = ldaq_ai_task(
    "ldaq_rig",
    sample_rate=RATE,
    channels=[("cDAQ1Mod2/ai0", -5, 5), ("cDAQ1Mod2/ai1", -5, 5)],
)
print(f"AITask       : {type(ai).__name__}")
print(f"  task_name  : {ai.task_name}")
print(f"  devices    : {ai.device_list}")
print(f"  products   : {ai.device_product_type}")
print(f"  sample_rate: {ai.sample_rate}")

acq = LDAQ.national_instruments.NIAcquisition(ai, acquisition_name="rig")
print(f"\nNIAcquisition: {acq.acquisition_name}")

acq.run_acquisition(1.0)
meas = acq.get_measurement_dict()

print("\nacquired:")
for k, v in meas.items():
    if isinstance(v, np.ndarray):
        print(f"  {k}: shape {v.shape}")
data = meas["data"]
print(f"\n  {data.shape[0]} samples x {data.shape[1]} ch at {RATE} Hz "
      f"= {data.shape[0]/RATE:.2f} s")
for i in range(data.shape[1]):
    col = data[:, i]
    print(f"  ch{i}: mean {col.mean():+.6f} V  rms {col.std():.6f} V")

ai.clear_task()
