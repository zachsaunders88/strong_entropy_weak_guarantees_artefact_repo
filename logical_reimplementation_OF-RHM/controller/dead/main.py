from .emn import EntropyMixingNetwork
from .reseeder import Reseeder
import time

emn = EntropyMixingNetwork()
r = Reseeder(
    emn,
    periodic_seconds=0.05,      # 50 ms
    jitter_frac=0.2,            # ±10 ms max jitter if implemented as [0, 0.2*period]
    deterministic_for_tests=False
)

# 1) Start
r.start_periodic()
if r._thread is None or not r._thread.is_alive():
    print("Reseeder thread didn't start")

# 2) Intermission
time.sleep(1)

# 3) Halt
r.stop_periodic()
if r._thread is not None:
    print("Reseeder thread didn't stop")

print(f"reseed_count = {r.reseed_count}")
print(f"last_reseed_ts = {r.last_reseed_ts}")
print(f"Sample observed intervals (first 5) = {r.observed_intervals} Observed sleep below min_interval_seconds")