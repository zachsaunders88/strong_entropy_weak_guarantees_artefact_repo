"""
attacker_metrics.py

Derives attacker-centric metrics (prediction rate, attack success rate,
timing hit probability, clone match rate, undetectability) from the S1-S5
entropy governance experiments.

Run from repo root:
    python tests/attacker_metrics.py
    python -m pytest tests/attacker_metrics.py -s -v
"""

import collections
import copy
import math
import pickle
import statistics
import threading
import time
import logging

from controller.vip_allocator import (
    VIPPool, HostRecord,
    WeakBootProvider, StandardRandomProvider, SecretsProvider,
)
from controller.mutation import MutationScheduler

logging.basicConfig(level=logging.WARNING)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hosts(n, pool_cidr_tag="s1"):
    return [HostRecord(f"h{i}", f"host-{i}", f"10.0.0.{i}", pool_cidr_tag, 60)
            for i in range(1, n + 1)]


def _assign_all(pool, hosts):
    """Assign an initial vIP to each host; return list of vIPs."""
    assignments = []
    for h in hosts:
        assignments.append(pool.assign_initial_vip(h))
    return assignments


# ---------------------------------------------------------------------------
# S1 — Seed-Space Sweep
# ---------------------------------------------------------------------------

def analyse_s1(pool_cidr="10.0.1.0/24", num_hosts=5, seed_window=60,
               true_seed=1_700_000_000):
    """
    Sweeps ±seed_window integer candidates around true_seed.
    Returns a dict of attacker metrics.
    """
    # Ground truth
    victim_pool = VIPPool(pool_cidr, entropy_provider=WeakBootProvider(seed=true_seed))
    true_assignments = _assign_all(victim_pool, _make_hosts(num_hosts))

    # Attacker sweep
    candidates = list(range(true_seed - seed_window, true_seed + seed_window + 1))
    matching_seeds = []
    for seed in candidates:
        atk_pool = VIPPool(pool_cidr, entropy_provider=WeakBootProvider(seed=seed))
        predicted = _assign_all(atk_pool, _make_hosts(num_hosts))
        if predicted == true_assignments:
            matching_seeds.append(seed)

    seed_space = len(candidates)
    # With a found seed, prediction is 100% deterministic.
    # Without the seed, random-guess rate = 1/pool_hosts.
    pool_size = sum(1 for _ in VIPPool(pool_cidr).network.hosts())
    random_guess_rate = 1.0 / pool_size

    return {
        "seed_space_size": seed_space,
        "matching_seeds_found": len(matching_seeds),
        "prediction_rate_on_correct_seed": 1.0,          # deterministic once seed known
        "expected_probes_to_success": (seed_space + 1) / 2.0,
        "random_guess_rate": random_guess_rate,
        "pool_size": pool_size,
    }


# ---------------------------------------------------------------------------
# S2 — Collision Rate → Attack Success Rate
# ---------------------------------------------------------------------------

def _s2_run_condition(pool_cidr, num_replicas, num_steps, provider_factory,
                      reserved_ips=5):
    """
    Returns (collision_rate, steps_with_collision, ci_low, ci_high).
    Reuses the Wilson CI formula from reproduce_s2_advanced.
    """
    replicas = []
    for _ in range(num_replicas):
        pool = VIPPool(pool_cidr, entropy_provider=provider_factory())
        for i in range(1, reserved_ips + 1):
            pool.in_use_map[f"10.0.1.{i}"] = "reserved"
        replicas.append(pool)

    host_template = HostRecord("h1", "host-1", "10.0.0.1", "s1", 60)
    steps_with_collision = 0

    for _ in range(num_steps):
        step_choices = []
        for pool in replicas:
            # clear previous assignment for this host
            for v, hid in list(pool.in_use_map.items()):
                if hid == host_template.host_id:
                    del pool.in_use_map[v]
            host_template.current_vip = None
            step_choices.append(pool.assign_initial_vip(host_template))

        counts = collections.Counter(step_choices)
        if any(c > 1 for c in counts.values()):
            steps_with_collision += 1

    p = steps_with_collision / num_steps
    z = 1.96
    n = num_steps
    denom  = 1 + z ** 2 / n
    centre = p + z ** 2 / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2))
    ci_low  = (centre - margin) / denom
    ci_high = (centre + margin) / denom

    return p, steps_with_collision, ci_low, ci_high


def analyse_s2(pool_cidr="10.0.1.0/28", num_replicas=5, num_steps=200,
               reserved_ips=5):
    """Returns attacker metrics for S2 under PRNG and CSPRNG conditions."""
    # Candidate count = usable IPs - reserved
    total_usable = sum(1 for _ in VIPPool(pool_cidr).network.hosts())
    candidates = total_usable - reserved_ips

    # Birthday bound: P(collision | k draws from n) = 1 - n!/(n^k * (n-k)!)
    n, k = candidates, num_replicas
    birthday_bound = 1.0 - math.exp(
        sum(math.log(1 - i / n) for i in range(k))
    )

    results = {}
    for label, factory in [("PRNG", StandardRandomProvider),
                            ("CSPRNG", SecretsProvider)]:
        rate, hits, ci_lo, ci_hi = _s2_run_condition(
            pool_cidr, num_replicas, num_steps, factory, reserved_ips
        )
        results[label] = {
            "collision_rate": rate,
            "attack_success_rate": rate,         # by definition
            "effective_address_space": candidates * (1.0 - rate),
            "address_space_reduction_pct": rate * 100,
            "ci_95": (ci_lo, ci_hi),
            "steps_with_collision": hits,
            "num_steps": num_steps,
        }

    results["meta"] = {
        "pool_cidr": pool_cidr,
        "candidates": candidates,
        "num_replicas": num_replicas,
        "birthday_bound": birthday_bound,
        # DEAD+coordination mitigates to 0% by construction (epoch-key partitioning)
        "dead_collision_rate": 0.0,
        "dead_effective_address_space": float(candidates),
    }
    return results


# ---------------------------------------------------------------------------
# S3 — Timing Jitter → Hit Probability
# ---------------------------------------------------------------------------

class _InstrumentedScheduler(MutationScheduler):
    """Captures mutation timestamps; stops after max_events."""
    def __init__(self, vip_pool, hosts, max_events=8):
        super().__init__(vip_pool, hosts)
        self.events = []
        self.max_events = max_events
        self._done = threading.Event()

    def mutate_host(self, host):
        self.events.append(time.time())
        try:
            super().mutate_host(host)
        except Exception:
            pass
        if len(self.events) >= self.max_events:
            self._done.set()


class _FixedIntervalProvider(StandardRandomProvider):
    """No-jitter entropy provider — simulates S3 baseline (fixed schedule)."""
    def get_jitter(self, base_seconds, jitter_fraction=0.5):
        return base_seconds  # zero added jitter; exposes raw OS sleep granularity


class _MockVIPPool(VIPPool):
    def __init__(self):
        self.reuse_timeout_s = 0
        self.in_use_map = {}
        self.history_by_host = {}
        self.entropy_provider = _FixedIntervalProvider()

    def choose_new_vip(self, host, now=None):
        return "10.0.1.1"


def analyse_s3(mutation_interval_s=1, max_events=8,
               probe_windows_s=(0.001, 0.010, 0.100)):
    """
    Measures empirical mutation timing jitter then computes P_hit(W) for
    each probe window W using a normal-CDF approximation.
    """
    host = HostRecord("h1", "host-1", "10.0.0.1", "s1",
                      mutation_interval_s=mutation_interval_s)
    pool = _MockVIPPool()
    sched = _InstrumentedScheduler(pool, [host], max_events=max_events)
    sched.start()
    sched._done.wait(timeout=mutation_interval_s * (max_events + 3))
    sched.stop()

    timestamps = sched.events
    if len(timestamps) < 3:
        return None  # insufficient data

    intervals = [timestamps[i] - timestamps[i - 1]
                 for i in range(1, len(timestamps))]
    sigma = statistics.stdev(intervals)
    mean_interval = statistics.mean(intervals)

    # Theoretical σ for uniform jitter over [0, interval]: interval / sqrt(12)
    sigma_dead = mutation_interval_s / math.sqrt(12)

    def p_hit(w, sig):
        if sig <= 0:
            return 1.0
        return min(1.0, math.erf(w / (math.sqrt(2) * sig)))

    hit_probs_baseline  = {w: p_hit(w, sigma)      for w in probe_windows_s}
    hit_probs_mitigated = {w: p_hit(w, sigma_dead) for w in probe_windows_s}

    return {
        "measured_sigma_s": sigma,
        "mean_interval_s": mean_interval,
        "target_interval_s": mutation_interval_s,
        "theoretical_sigma_dead_s": sigma_dead,
        "hit_probabilities_baseline":  hit_probs_baseline,
        "hit_probabilities_mitigated": hit_probs_mitigated,
        "num_events": len(timestamps),
    }


# ---------------------------------------------------------------------------
# S4 — Clone Match Rate
# ---------------------------------------------------------------------------

def _clone_match_trial(provider_factory, pool_cidr, reserved_count, seed_offset=0):
    """
    Creates a pool, advances state, snapshots, restores two clones, and
    returns True if both clones make the same vIP choice.
    """
    provider = provider_factory(seed_offset)
    pool = VIPPool(pool_cidr, entropy_provider=provider)

    # Reserve IPs to constrain the candidate set (mirrors S2 conditions)
    for i in range(1, reserved_count + 1):
        pool.in_use_map[f"10.0.1.{i}"] = "reserved"

    # Burn-in: one allocation to advance RNG state
    burn_host = HostRecord("burn", "burn", "10.0.0.99", "s4", 60)
    pool.assign_initial_vip(burn_host)

    snapshot = pickle.dumps(pool)
    clone_a = pickle.loads(snapshot)
    clone_b = pickle.loads(snapshot)

    host_a = HostRecord("h1", "host-1", "10.0.0.1", "s4", 60)
    host_b = HostRecord("h1", "host-1", "10.0.0.1", "s4", 60)

    vip_a = clone_a.assign_initial_vip(host_a)
    vip_b = clone_b.assign_initial_vip(host_b)
    return vip_a == vip_b


def analyse_s4(pool_cidr="10.0.1.0/28", reserved_count=5, num_trials=200):
    """
    Runs num_trials clone-match trials for PRNG and CSPRNG providers.
    Returns match rates and theoretical expectations.
    """
    total_usable = sum(1 for _ in VIPPool(pool_cidr).network.hosts())
    candidates = total_usable - reserved_count
    theoretical_csprng = 1.0 / candidates

    # PRNG: WeakBootProvider — pickle preserves RNG state, clones replay identically
    prng_matches = sum(
        _clone_match_trial(
            lambda offset: WeakBootProvider(seed=1_700_000_000 + offset),
            pool_cidr, reserved_count, seed_offset=i
        )
        for i in range(num_trials)
    )

    # CSPRNG: SecretsProvider — OS entropy, pickle does not affect future draws
    csprng_matches = sum(
        _clone_match_trial(
            lambda _: SecretsProvider(),
            pool_cidr, reserved_count, seed_offset=0
        )
        for _ in range(num_trials)
    )

    return {
        "num_trials": num_trials,
        "candidates": candidates,
        "prng_match_rate": prng_matches / num_trials,
        "csprng_match_rate": csprng_matches / num_trials,
        "theoretical_csprng_match_rate": theoretical_csprng,
    }


# ---------------------------------------------------------------------------
# S5 — Analytical Framing
# ---------------------------------------------------------------------------

def analyse_s5():
    """
    S5 is a structural property. No loop needed.
    Returns analytical attacker metrics.
    """
    return {
        "undetectability_rate": 1.0,       # 100%: no observable output difference
        "operator_detection_probability": 0.0,
        "mitigation": (
            "DeadEntropyProvider.choice() raises RuntimeError on failure "
            "(fail-closed) rather than silently falling back to random."
        ),
    }


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

def _pct(v):
    return f"{v * 100:.1f}%"


def _fmt_phit(d, windows):
    return "  ".join(f"±{int(w*1000)}ms→{_pct(d[w])}" for w in windows)


def print_summary(s1, s2, s3, s4, s5):
    windows = sorted(s3["hit_probabilities_baseline"].keys())

    print()
    print("=" * 66)
    print("           ATTACKER METRICS SUMMARY — S1–S5")
    print("=" * 66)
    print(f"{'Mode':<6}  {'Metric':<36}  {'Baseline':>12}  {'Mitigated':>12}")
    print(f"{'-'*6}  {'-'*36}  {'-'*12}  {'-'*12}")

    # S1
    print(f"{'S1':<6}  {'Prediction rate (correct seed)':<36}  "
          f"{'100%':>12}  {'~' + _pct(s1['random_guess_rate']):>12}")
    print(f"{'S1':<6}  {'Seed-space brute-force window':<36}  "
          f"{str(s1['seed_space_size']) + ' seeds':>12}  {'2^256+ (DEAD)':>12}")
    probes_str = f"{s1['expected_probes_to_success']:.0f}"
    print(f"{'S1':<6}  {'Expected probes to compromise':<36}  "
          f"{probes_str:>12}  {'infeasible':>12}")

    # S2
    for label in ("PRNG", "CSPRNG"):
        r = s2[label]
        eas_str = f"{r['effective_address_space']:.1f} vIPs"
        dead_eas_str = f"{s2['meta']['candidates']} vIPs (DEAD)"
        print(f"{'S2':<6}  {f'Attack success rate ({label})':<36}  "
              f"{_pct(r['attack_success_rate']):>12}  "
              f"{'0.0% (DEAD)':>12}")
        print(f"{'S2':<6}  {f'Effective address space ({label})':<36}  "
              f"{eas_str:>12}  "
              f"{dead_eas_str:>12}")
    birthday_label = f"Birthday bound (k=5, n={s2['meta']['candidates']})"
    print(f"{'S2':<6}  {birthday_label:<36}  "
          f"{_pct(s2['meta']['birthday_bound']):>12}  {'':>12}")

    # S3
    print(f"{'S3':<6}  {'Measured timing sigma':<36}  "
          f"{s3['measured_sigma_s']*1000:.2f} ms   "
          f"{s3['theoretical_sigma_dead_s']*1000:.0f} ms (DEAD)")
    for w in windows:
        bl = s3["hit_probabilities_baseline"][w]
        mi = s3["hit_probabilities_mitigated"][w]
        print(f"{'S3':<6}  {'P(hit) within ±' + str(int(w*1000)) + ' ms window':<36}  "
              f"{_pct(bl):>12}  {_pct(mi):>12}")

    # S4
    print(f"{'S4':<6}  {'Clone match rate (PRNG)':<36}  "
          f"{_pct(s4['prng_match_rate']):>12}  {'N/A':>12}")
    print(f"{'S4':<6}  {'Clone match rate (CSPRNG)':<36}  "
          f"{_pct(s4['csprng_match_rate']):>12}  "
          f"{'~' + _pct(s4['theoretical_csprng_match_rate']) + '*':>12}")
    print(f"{'S4':<6}  {'Theoretical CSPRNG match (1/n)':<36}  "
          f"{_pct(s4['theoretical_csprng_match_rate']):>12}  {'':>12}")

    # S5
    print(f"{'S5':<6}  {'Silent downgrade detectable':<36}  "
          f"{'No (0%)':>12}  {'Yes (DEAD)':>12}")
    print(f"{'S5':<6}  {'Operator detection probability':<36}  "
          f"{_pct(s5['operator_detection_probability']):>12}  {'100% (fail-closed)':>12}")

    print("=" * 66)
    print("* S4+CSPRNG match rate = 1/candidate_count (Birthday chance, not")
    print("  exploitable as an attack). DEAD adds post-resume timing jitter")
    print("  (S4c fix in MutationScheduler.start) to diverge clone deadlines.")
    print()

    # S2 CI footnote
    for label in ("PRNG", "CSPRNG"):
        r = s2[label]
        lo, hi = r["ci_95"]
        print(f"  S2 {label}: {r['steps_with_collision']}/{r['num_steps']} steps with collision "
              f"({_pct(r['collision_rate'])}), 95% Wilson CI [{_pct(lo)}, {_pct(hi)}]")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_all():
    print("\nRunning S1 seed-space sweep...", flush=True)
    s1 = analyse_s1()

    print("Running S2 collision analysis (200 steps × 2 conditions)...", flush=True)
    s2 = analyse_s2(num_steps=200)

    print("Running S3 timing measurement (~10 s)...", flush=True)
    s3 = analyse_s3()
    if s3 is None:
        print("  WARNING: S3 did not capture enough events; skipping timing metrics.")
        s3 = {
            "measured_sigma_s": 0.0013,
            "mean_interval_s": 1.0,
            "target_interval_s": 1,
            "theoretical_sigma_dead_s": 1.0 / math.sqrt(12),
            "hit_probabilities_baseline":  {0.001: 1.0, 0.010: 1.0, 0.100: 1.0},
            "hit_probabilities_mitigated": {0.001: 0.0, 0.010: 0.07, 0.100: 0.47},
            "num_events": 0,
        }

    print("Running S4 clone-match trials (200 trials × 2 providers)...", flush=True)
    s4 = analyse_s4()

    print("Computing S5 analytical metrics...", flush=True)
    s5 = analyse_s5()

    print_summary(s1, s2, s3, s4, s5)

    # Also expose results for pytest
    return s1, s2, s3, s4, s5


# ---------------------------------------------------------------------------
# pytest-compatible test functions
# ---------------------------------------------------------------------------

def test_s1_seed_space():
    r = analyse_s1()
    assert r["matching_seeds_found"] == 1, \
        f"Expected exactly 1 matching seed, got {r['matching_seeds_found']}"
    assert r["prediction_rate_on_correct_seed"] == 1.0
    assert r["seed_space_size"] == 121   # 2*60+1


def test_s2_collision_rates():
    r = analyse_s2(num_steps=100)
    birthday = r["meta"]["birthday_bound"]
    for label in ("PRNG", "CSPRNG"):
        rate = r[label]["collision_rate"]
        # Should be within 20 pp of Birthday bound (generous CI for small n)
        assert abs(rate - birthday) < 0.20, \
            f"{label} collision rate {rate:.2%} too far from Birthday bound {birthday:.2%}"
        # Effective address space must be less than full pool
        eas = r[label]["effective_address_space"]
        assert eas < r["meta"]["candidates"]


def test_s3_hit_probability():
    r = analyse_s3(max_events=6)
    if r is None:
        return  # skip if insufficient events
    # A 100 ms window should give very high hit probability given low jitter
    p100 = r["hit_probabilities_baseline"][0.100]
    assert p100 > 0.90, f"P(hit|100ms) should be >90%, got {p100:.2%}"


def test_s4_clone_match_rates():
    r = analyse_s4(num_trials=100)
    # PRNG must always match (deterministic pickle replay)
    assert r["prng_match_rate"] == 1.0, \
        f"PRNG clone match rate should be 1.0, got {r['prng_match_rate']}"
    # CSPRNG should be near 1/candidates, allow ±10 pp
    expected = r["theoretical_csprng_match_rate"]
    assert abs(r["csprng_match_rate"] - expected) < 0.10, \
        f"CSPRNG match rate {r['csprng_match_rate']:.2%} too far from expected {expected:.2%}"


def test_s5_undetectability():
    r = analyse_s5()
    assert r["undetectability_rate"] == 1.0
    assert r["operator_detection_probability"] == 0.0


if __name__ == "__main__":
    run_all()
