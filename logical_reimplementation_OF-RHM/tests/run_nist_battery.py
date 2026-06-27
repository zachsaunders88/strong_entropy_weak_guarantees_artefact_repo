"""
T5 — NIST SP 800-22 Rev 1a battery against retained binary output files.

Runs all eligible tests against the 20 EMN and 20 os.urandom trial files in
batter_eval_out/. Uses 100,000 bits (12,500 bytes) per trial.

Reports per-test pass rates across 20 trials for both generators.
"""

import os
import sys
import numpy as np
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nistrng import SP800_22R1A_BATTERY, check_eligibility_all_battery, run_all_battery, pack_sequence

OUTDIR         = "batter_eval_out"
BITS_PER_TRIAL = 1_000_000      # 125,000 bytes per trial (1 Mbit)
BYTES_PER_TRIAL = BITS_PER_TRIAL // 8
N_TRIALS       = 20
ALPHA          = 0.05


def load_seq(filepath):
    with open(filepath, "rb") as f:
        raw = f.read(BYTES_PER_TRIAL)
    arr = np.frombuffer(raw, dtype=np.uint8)
    return pack_sequence(arr)          # uint8 bytes -> int8 bits


def run_battery_on_file(filepath):
    """Returns dict: display_name -> passed (bool)."""
    seq = load_seq(filepath)
    eligible = check_eligibility_all_battery(seq, SP800_22R1A_BATTERY)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        results = run_all_battery(seq, eligible, SP800_22R1A_BATTERY)
    return {r.name: r.passed for r, _ in results}


def run_variant(prefix):
    per_test = {}          # name -> [pass/fail booleans]
    errors = 0
    for i in range(N_TRIALS):
        path = os.path.join(OUTDIR, f"{prefix}_trial{i}.bin")
        if not os.path.exists(path):
            errors += 1
            continue
        try:
            outcomes = run_battery_on_file(path)
            for name, passed in outcomes.items():
                per_test.setdefault(name, []).append(bool(passed))
        except Exception as e:
            errors += 1
            print(f"  ERROR trial {i}: {e}")
    return per_test, errors


def main():
    print(f"NIST SP 800-22 Rev 1a battery ({BITS_PER_TRIAL:,} bits / trial, {N_TRIALS} trials)\n")

    print("[1/2] os.urandom ...")
    sys_results, sys_err = run_variant("sys")
    print(f"  Done ({N_TRIALS - sys_err} trials)")

    print("[2/2] EMN ...")
    emn_results, emn_err = run_variant("emn")
    print(f"  Done ({N_TRIALS - emn_err} trials)\n")

    # Collect all test names seen
    all_names = sorted(set(list(sys_results.keys()) + list(emn_results.keys())))

    print(f"{'=' * 66}")
    print(f"{'Test':<35} {'os.urandom':>12} {'EMN':>12}")
    print(f"{'-' * 66}")

    # Track tests to flag as potentially unreliable (both generators fail consistently)
    unreliable = []

    for name in all_names:
        sv = sys_results.get(name, [])
        ev = emn_results.get(name, [])
        if not sv and not ev:
            continue

        sys_pass = sum(sv)
        emn_pass = sum(ev)
        sys_str = f"{sys_pass}/{len(sv)}" if sv else "N/A"
        emn_str = f"{emn_pass}/{len(ev)}" if ev else "N/A"

        # Flag tests where both generators never pass (likely library limitation)
        if sv and ev and sys_pass == 0 and emn_pass == 0:
            marker = " *"
            unreliable.append(name)
        else:
            marker = ""

        print(f"{name:<35} {sys_str:>12} {emn_str:>12}{marker}")

    print(f"{'=' * 66}")
    print(f"Expected false positives at alpha=0.05: {ALPHA * N_TRIALS:.1f} per test")

    if unreliable:
        print(f"\n* Both generators score 0 passes — likely library limitation at "
              f"{BITS_PER_TRIAL:,} bits:")
        for t in unreliable:
            print(f"  - {t}")


if __name__ == "__main__":
    main()
