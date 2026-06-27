"""
Extended S2 Collision Runs (10 x 1,000 steps) -- reproduces Table 2 (S2 row).

Runs ten independent 1,000-step S2 collision experiments per condition at
k=5, n=9 (constrained /28 pool), and reports per-run rates plus grand means
with 95% Wilson confidence intervals. This is the experiment behind the
camera-ready Table 2 constrained-pool S2 figures and the 0/10,000 DEAD v1.1
upper bound.

Conditions:
  PRNG      -- StandardRandomProvider (random.choice), no coordination
  CSPRNG    -- SecretsProvider (os.urandom), no coordination
  DEAD v1.0 -- CSPRNG-quality entropy, no epoch-key coordination.
               Mechanically identical to SecretsProvider without coordination
               (the Birthday rate is source-quality-invariant).
  DEAD v1.1 -- MockCoordinatedProvider with epoch-key coordination (same
               mechanism as the HTTP-backed DeadEntropyProvider, without the
               transport layer, which is irrelevant to the statistical result).

Expected output (Table 2, constrained /28 pool, k=5, n=9, grand means):
  PRNG: ~74.8%   CSPRNG: ~74.1%   DEAD v1.0: ~74.8%   DEAD v1.1: 0.0% (0/10,000)

This script reproduces the numeric result only; the camera-ready boxplot figure
is generated separately in the paper sources and is not reproduced here.

Run from the logical reimplementation root:
    python tests/s2_extended_runs.py
"""

import collections
import copy
import math
import os
import sys

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(REPO_ROOT))

from controller.vip_allocator import (
    VIPPool, HostRecord, StandardRandomProvider, SecretsProvider, EntropyProvider
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

POOL_CIDR          = "10.0.1.0/28"
N_RESERVED         = 5
N_EFFECTIVE        = 9
K                  = 5
NUM_RUNS           = 10
STEPS_PER_RUN      = 1_000
THEORETICAL_BOUND  = 0.7439   # 1 - 9!/(4! * 9^5)
COORDINATION_SCOPE = "s2-extended-scope"


# ---------------------------------------------------------------------------
# MockCoordinatedProvider (epoch-key coordination without the HTTP transport)
# ---------------------------------------------------------------------------

class MockCoordinatedProvider(EntropyProvider):
    _epoch_keys: dict = {}

    @classmethod
    def reset_epoch(cls, scope: str, seed: str = "mock-epoch-key"):
        cls._epoch_keys[scope] = seed

    def get_epoch_key(self, scope: str) -> str:
        if scope not in self._epoch_keys:
            MockCoordinatedProvider.reset_epoch(scope)
        return self._epoch_keys[scope]

    def choice(self, seq, **kwargs):
        import secrets as _s
        return _s.choice(seq)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def wilson_ci(hits: int, n: int, z: float = 1.96):
    if n == 0:
        return 0.0, 1.0
    p = hits / n
    denom  = 1 + z**2 / n
    centre = p + z**2 / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
    return (centre - margin) / denom, (centre + margin) / denom


def rule_of_three_upper(n: int) -> float:
    return 1.0 - (0.05 ** (1.0 / n))


# ---------------------------------------------------------------------------
# Single-run simulation
# ---------------------------------------------------------------------------

def _one_run_uncoordinated(entropy_factory, steps: int) -> float:
    replicas = []
    for _ in range(K):
        pool = VIPPool(POOL_CIDR, entropy_provider=entropy_factory())
        for i in range(1, N_RESERVED + 1):
            pool.in_use_map[f"10.0.1.{i}"] = "reserved"
        replicas.append(pool)

    host_def = HostRecord("h1", "host-1", "10.0.0.1", "s1", 60)
    hits = 0
    for _ in range(steps):
        choices = []
        for pool in replicas:
            for v, hid in list(pool.in_use_map.items()):
                if hid == host_def.host_id:
                    del pool.in_use_map[v]
            host_def.current_vip = None
            choices.append(pool.assign_initial_vip(host_def))
        if any(c > 1 for c in collections.Counter(choices).values()):
            hits += 1
    return hits / steps


def _one_run_coordinated(steps: int) -> float:
    MockCoordinatedProvider.reset_epoch(COORDINATION_SCOPE)
    replicas = []
    for i in range(K):
        pool = VIPPool(
            POOL_CIDR,
            entropy_provider=MockCoordinatedProvider(),
            replica_id=str(i),
            coordination_scope=COORDINATION_SCOPE,
        )
        for j in range(1, N_RESERVED + 1):
            pool.in_use_map[f"10.0.1.{j}"] = "reserved"
        replicas.append(pool)

    host_def = HostRecord("h1", "host-1", "10.0.0.1", "s1", 60)
    hits = 0
    for _ in range(steps):
        choices = []
        for pool in replicas:
            for v, hid in list(pool.in_use_map.items()):
                if hid == host_def.host_id:
                    del pool.in_use_map[v]
            h = copy.copy(host_def)
            h.current_vip = None
            choices.append(pool.choose_new_vip(h))
        if any(c > 1 for c in collections.Counter(choices).values()):
            hits += 1
    return hits / steps


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

CONDITIONS = [
    ("PRNG",      lambda: _one_run_uncoordinated(StandardRandomProvider, STEPS_PER_RUN)),
    ("CSPRNG",    lambda: _one_run_uncoordinated(SecretsProvider,        STEPS_PER_RUN)),
    ("DEAD v1.0", lambda: _one_run_uncoordinated(SecretsProvider,        STEPS_PER_RUN)),
    ("DEAD v1.1", lambda: _one_run_coordinated(STEPS_PER_RUN)),
]


def main():
    print("=" * 65)
    print(f"Extended S2 Runs  ({NUM_RUNS} x {STEPS_PER_RUN} steps)")
    print(f"k={K} replicas, n={N_EFFECTIVE} effective candidates")
    print(f"Theoretical Birthday bound: {THEORETICAL_BOUND*100:.1f}%")
    print("=" * 65)

    for label, run_fn in CONDITIONS:
        print(f"\n--- {label} ---")
        rates = []
        for r in range(1, NUM_RUNS + 1):
            rate = run_fn()
            rates.append(rate)
            print(f"  Run {r:2d}: {rate*100:.1f}%")

        total_hits  = round(sum(rates) * STEPS_PER_RUN)
        total_steps = NUM_RUNS * STEPS_PER_RUN
        grand_mean  = total_hits / total_steps

        if total_hits == 0:
            ub = rule_of_three_upper(total_steps)
            print(f"  Grand mean: 0.0%  (95% upper bound <{ub*100:.3f}% by rule of three)")
            print(f"  ({total_hits}/{total_steps} collision steps)")
        else:
            lo, hi = wilson_ci(total_hits, total_steps)
            print(f"  Grand mean: {grand_mean*100:.1f}%  (95% Wilson CI [{lo*100:.1f}%, {hi*100:.1f}%])")
            print(f"  ({total_hits}/{total_steps} collision steps)")


if __name__ == "__main__":
    main()
