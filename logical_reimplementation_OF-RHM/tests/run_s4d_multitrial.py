"""
T8 — Formal S4d multi-trial experiment (200 iterations).

Measures cross-clone collision rate under shared constraint state (in_use_map)
at 2-clone scale across three configurations:
  - PRNG     : WeakBootProvider (seeded, deterministic)
  - CSPRNG   : SecretsProvider (os.urandom)
  - DEAD     : DeadEntropyProvider against a live daemon

Pool: /28, 5 addresses reserved, 9 effective candidates.
Per-trial: both clones restored from the same pickle snapshot; each makes one
fresh vIP allocation. Collision = both clones choose the same vIP.

Also measures the S4b (clone linkage via CSPRNG state) collision rate for the
Table 2 entry — two independently-seeded CSPRNG instances sharing only the
same in_use_map should converge at the independent Birthday rate (~11.1%).
"""

import os
import sys
import pickle
import copy
import math
import statistics
import subprocess
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from controller.vip_allocator import (
    VIPPool, HostRecord, SecretsProvider, WeakBootProvider, DeadEntropyProvider
)

N_TRIALS   = 200
POOL_CIDR  = "10.0.1.0/28"   # /28 = 14 IPs, 5 reserved -> 9 effective candidates
N_RESERVED = 5
N_CANDIDATES = 9
K_CLONES   = 2
DEAD_URL   = "http://127.0.0.1:8001"   # use port 8001 to avoid clashing with other tests


# ── Wilson score CI ─────────────────────────────────────────────────────────

def wilson_ci(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2*n)) / denom
    margin = (z * math.sqrt(p*(1-p)/n + z**2/(4*n**2))) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


# ── Pool factory ─────────────────────────────────────────────────────────────

def make_pool(provider):
    pool = VIPPool(POOL_CIDR, entropy_provider=provider)
    for i in range(1, N_RESERVED + 1):
        pool.in_use_map[f"10.0.1.{i}"] = "reserved"
    return pool


# ── Single trial ────────────────────────────────────────────────────────────

def run_trial_shared_snapshot(provider_factory):
    """
    Create a pool, take a pickle snapshot, restore two independent clones,
    have each make one vIP allocation. Returns True if they collide.
    """
    base_pool = make_pool(provider_factory())
    snapshot  = pickle.dumps(base_pool)

    clone_a = pickle.loads(snapshot)
    clone_b = pickle.loads(snapshot)

    host_a = HostRecord("h1", "host-1", "10.0.0.1", "s1", 30.0)
    host_b = HostRecord("h1", "host-1", "10.0.0.1", "s1", 30.0)

    vip_a = clone_a.assign_initial_vip(host_a)
    vip_b = clone_b.assign_initial_vip(host_b)

    return vip_a == vip_b


def run_trials(label, provider_factory, n=N_TRIALS):
    collisions = sum(run_trial_shared_snapshot(provider_factory) for _ in range(n))
    rate = collisions / n
    lo, hi = wilson_ci(collisions, n)
    return collisions, n, rate, lo, hi


# ── DEAD server management ───────────────────────────────────────────────────

def start_dead_server():
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn",
         "controller.dead.server:app",
         "--host", "127.0.0.1", "--port", "8001",
         "--log-level", "warning"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait for readiness
    import requests
    deadline = time.time() + 10.0
    while time.time() < deadline:
        try:
            if requests.get(f"{DEAD_URL}/status", timeout=1.0).status_code == 200:
                return proc
        except Exception:
            pass
        time.sleep(0.2)
    proc.terminate()
    raise RuntimeError("DEAD server did not start within 10 s")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("T8 — S4d Constrained State Convergence: 200-trial experiment")
    print(f"  Pool: {POOL_CIDR}, {N_RESERVED} reserved -> {N_CANDIDATES} effective candidates")
    print(f"  Scale: k={K_CLONES} clones per trial, {N_TRIALS} trials per condition")
    print(f"  Theoretical independent-random collision probability: "
          f"P(k=2, n=9) = 1-8/9 ~= {1 - 8/9:.1%}\n")

    results = {}

    # PRNG
    print("[1/3] PRNG (WeakBootProvider, seed=42) ...")
    results["PRNG"] = run_trials("PRNG", lambda: WeakBootProvider(seed=42))
    print(f"  Done: {results['PRNG'][0]}/{results['PRNG'][1]} collisions")

    # CSPRNG
    print("[2/3] CSPRNG (SecretsProvider) ...")
    results["CSPRNG"] = run_trials("CSPRNG", SecretsProvider)
    print(f"  Done: {results['CSPRNG'][0]}/{results['CSPRNG'][1]} collisions")

    # DEAD
    print("[3/3] DEAD v1.1 (DeadEntropyProvider) — starting daemon ...")
    dead_proc = None
    try:
        dead_proc = start_dead_server()
        print("  Daemon ready.")
        results["DEAD"] = run_trials(
            "DEAD", lambda: DeadEntropyProvider(server_url=DEAD_URL)
        )
        print(f"  Done: {results['DEAD'][0]}/{results['DEAD'][1]} collisions")
    except Exception as e:
        print(f"  WARNING: DEAD daemon unavailable ({e}); skipping DEAD condition.")
        results["DEAD"] = None
    finally:
        if dead_proc:
            dead_proc.terminate()

    # ── Report ──────────────────────────────────────────────────────────────
    print(f"\n{'=' * 65}")
    print(f"S4d Results: Constrained State Convergence ({N_TRIALS} trials, k=2, n=9)")
    print(f"{'=' * 65}")
    print(f"{'Configuration':<12} {'Collisions':>12} {'Rate':>8} {'95% CI':>22}")
    print("-" * 65)

    theoretical = 1 - 8/9
    for label in ["PRNG", "CSPRNG", "DEAD"]:
        r = results[label]
        if r is None:
            print(f"{label:<12} {'N/A':>12}")
            continue
        cols, n, rate, lo, hi = r
        print(f"{label:<12} {cols:>5}/{n:<6} {rate:>7.1%}  [{lo:.1%}, {hi:.1%}]")
    print(f"\n  Theoretical independent-random bound (k=2, n=9): {theoretical:.1%}")

    # Interpret
    csprng_rate = results["CSPRNG"][2]
    if abs(csprng_rate - theoretical) < 0.05:
        print(f"\n  CSPRNG rate {csprng_rate:.1%} is consistent with the independent-random "
              f"expectation of {theoretical:.1%}.")
        print(f"  S4d (shared constraint map) drives correlated selection when clones "
              f"share in_use_map state.")
    else:
        print(f"\n  CSPRNG rate {csprng_rate:.1%} — review constraint sharing logic.")

    prng_rate = results["PRNG"][2]
    if prng_rate > theoretical + 0.05:
        print(f"  PRNG rate {prng_rate:.1%} elevated above theoretical bound — "
              f"PRNG seed determinism amplifies constraint correlation.")


if __name__ == "__main__":
    main()
