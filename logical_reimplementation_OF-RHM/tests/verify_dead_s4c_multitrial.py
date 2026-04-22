import time
import copy
import sys
import os
import statistics

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from controller.vip_allocator import VIPPool, HostRecord, SecretsProvider
from controller.mutation import MutationScheduler

N_TRIALS = int(os.getenv("S4C_TRIALS", "200"))
MUTATION_INTERVAL = 1.0
POOL_CIDR = "10.0.1.0/24"
TRIAL_TIMEOUT = 10.0


class TimestampedScheduler(MutationScheduler):
    """Polls at 10 ms to make timing divergence observable within a single trial."""

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
            time.sleep(0.01)

    def mutate_host(self, host):
        if self.first_mutation_ts is None:
            self.first_mutation_ts = time.time()
        self.running = False


def run_trial():
    """Returns timing delta in milliseconds for one snapshot-restore pair."""
    base_host = HostRecord(
        host_id="h1",
        name="host-1",
        real_ip="10.0.0.1",
        subnet_id="s1",
        mutation_interval_s=MUTATION_INTERVAL,
    )
    base_host.history.append(("10.0.1.5", time.time() - 0.5))
    base_host.current_vip = "10.0.1.5"

    host_a = copy.deepcopy(base_host)
    host_b = copy.deepcopy(base_host)

    pool_a = VIPPool(POOL_CIDR, entropy_provider=SecretsProvider())
    pool_b = VIPPool(POOL_CIDR, entropy_provider=SecretsProvider())
    pool_a.in_use_map["10.0.1.5"] = "h1"
    pool_b.in_use_map["10.0.1.5"] = "h1"

    sched_a = TimestampedScheduler(pool_a, [host_a])
    sched_b = TimestampedScheduler(pool_b, [host_b])

    sched_a.start()
    sched_b.start()

    deadline = time.time() + TRIAL_TIMEOUT
    while time.time() < deadline:
        if sched_a.first_mutation_ts is not None and sched_b.first_mutation_ts is not None:
            break
        time.sleep(0.005)

    sched_a.running = False
    sched_b.running = False

    if sched_a.first_mutation_ts is None or sched_b.first_mutation_ts is None:
        return None  # timeout

    return abs(sched_a.first_mutation_ts - sched_b.first_mutation_ts) * 1000.0


def main():
    print(f"Running {N_TRIALS} S4c trials (mutation_interval={MUTATION_INTERVAL}s)...")
    print("Theoretical expectation for independent Uniform[0, μ] offsets: E[|Δ|] = μ/3 ≈ 333 ms.")
    print("Observed values may be lower on some systems due to scheduling and timer coalescing.\n")

    deltas = []
    timeouts = 0

    for i in range(N_TRIALS):
        delta = run_trial()
        if delta is None:
            timeouts += 1
            print(f"  Trial {i+1:3d}: TIMEOUT")
        else:
            deltas.append(delta)
            if (i + 1) % 20 == 0:
                print(f"  Completed {i+1}/{N_TRIALS} trials, running mean: {statistics.mean(deltas):.1f} ms")

    print(f"\n=== S4c Multi-Trial Results ({N_TRIALS} trials) ===")
    if timeouts:
        print(f"Timeouts: {timeouts}")

    if not deltas:
        print("No valid trials — cannot compute statistics.")
        sys.exit(1)

    mean = statistics.mean(deltas)
    stdev = statistics.stdev(deltas) if len(deltas) > 1 else 0.0
    minimum = min(deltas)
    maximum = max(deltas)

    print(f"Valid trials : {len(deltas)}")
    print(f"Mean         : {mean:.1f} ms")
    print(f"Std dev      : {stdev:.1f} ms")
    print(f"Min          : {minimum:.1f} ms")
    print(f"Max          : {maximum:.1f} ms")

    DIVERGENCE_THRESHOLD_MS = 10.0
    frac_over = sum(1 for d in deltas if d >= DIVERGENCE_THRESHOLD_MS) / len(deltas)
    print(f"Fraction ≥ {DIVERGENCE_THRESHOLD_MS:.0f} ms: {frac_over*100:.1f}%")

    if frac_over < 0.95:
        print("\nFAIL: Too many trials fall below the 10 ms divergence threshold.")
        sys.exit(1)

    print("\nPASS: Clones diverge reliably after resume; S4c mitigation confirmed.")


if __name__ == "__main__":
    main()
