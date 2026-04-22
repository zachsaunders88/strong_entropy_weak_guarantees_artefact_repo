import time
import copy
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from controller.vip_allocator import VIPPool, HostRecord, SecretsProvider
from controller.mutation import MutationScheduler


class TimestampedScheduler(MutationScheduler):
    """Subclass that records first mutation timestamp then halts.

    Overrides _loop() to poll at 10ms granularity so the timing
    divergence produced by the S4c fix is observable within a single
    test run. The base-class _loop() sleeps ~1s per cycle — the same
    order as mutation_interval_s — so both clones would land in the
    same cycle regardless of offset, making the fix unobservable.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.first_mutation_ts = None

    def _loop(self):
        while self.running:
            now = time.time()
            for host in self.hosts:
                last_mutation = 0.0
                if host.history:
                    _, last_mutation = host.history[-1]
                if now - last_mutation >= host.mutation_interval_s:
                    self.mutate_host(host)
                    if not self.running:
                        return
            time.sleep(0.01)  # 10ms poll — fine-grained enough to observe offset divergence

    def mutate_host(self, host):
        if self.first_mutation_ts is None:
            self.first_mutation_ts = time.time()
        self.running = False


MUTATION_INTERVAL = 1.0
POOL_CIDR = "10.0.1.0/24"

# Create a base host simulating a snapshot state.
# The host last mutated 0.5s ago — almost due for its next cycle
# with mutation_interval_s=1.0.
base_host = HostRecord(
    host_id="h1",
    name="host-1",
    real_ip="10.0.0.1",
    subnet_id="s1",
    mutation_interval_s=MUTATION_INTERVAL,
)
base_host.history.append(("10.0.1.5", time.time() - 0.5))
base_host.current_vip = "10.0.1.5"

# Deep-copy to produce two independent clones with identical initial state.
host_a = copy.deepcopy(base_host)
host_b = copy.deepcopy(base_host)

# Two independent VIPPool instances using SecretsProvider.
pool_a = VIPPool(POOL_CIDR, entropy_provider=SecretsProvider())
pool_b = VIPPool(POOL_CIDR, entropy_provider=SecretsProvider())

# Pre-populate in_use_map to match the snapshot state.
pool_a.in_use_map["10.0.1.5"] = "h1"
pool_b.in_use_map["10.0.1.5"] = "h1"

sched_a = TimestampedScheduler(pool_a, [host_a])
sched_b = TimestampedScheduler(pool_b, [host_b])

sched_a.start()
sched_b.start()

# Wait for both schedulers to record their first event.
timeout = 10.0
start_wait = time.time()
while time.time() - start_wait < timeout:
    if (sched_a.first_mutation_ts is not None and
            sched_b.first_mutation_ts is not None):
        break
    time.sleep(0.05)

if sched_a.first_mutation_ts is None:
    print("TIMEOUT: Clone A did not fire within 10s")
    sys.exit(1)
if sched_b.first_mutation_ts is None:
    print("TIMEOUT: Clone B did not fire within 10s")
    sys.exit(1)

delta = abs(sched_a.first_mutation_ts - sched_b.first_mutation_ts)

print("\n=== S4c Verification: Schedule Timing Divergence ===")
print(f"Snapshot timestamp: {base_host.history[0][1]:.4f}")
print(f"Clone A first mutation: {sched_a.first_mutation_ts:.4f}")
print(f"Clone B first mutation: {sched_b.first_mutation_ts:.4f}")
print(f"Timing delta: {delta * 1000:.1f}ms")
print(f"Mutation interval: {MUTATION_INTERVAL}s")

DIVERGENCE_THRESHOLD_MS = 10.0
if delta * 1000 < DIVERGENCE_THRESHOLD_MS:
    print(f"\nFAIL: Clones fired within {DIVERGENCE_THRESHOLD_MS}ms of each other.")
    print("S4c mitigation is NOT effective.")
    sys.exit(1)
else:
    print(f"\nSUCCESS: Clones diverged by {delta * 1000:.1f}ms.")
    print("S4c mitigation confirmed.")
    sys.exit(0)
