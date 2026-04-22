#!/usr/bin/env python3
"""Reproduces Table 5: NIST SP 800-22 Rev 1a entropy quality evaluation.

Runs the NIST SP 800-22 Rev 1a battery (via `nistrng`) on byte streams from:
  1) os.urandom
  2) the DEAD Entropy Mixing Network (EMN)

Run from the repository root:
  python nist/run_nist.py

This script is designed to run without requiring any pre-generated `.bin` files.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import warnings
from dataclasses import dataclass

import numpy as np

from nistrng import (
    SP800_22R1A_BATTERY,
    check_eligibility_all_battery,
    run_all_battery,
    pack_sequence,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOGICAL = os.path.join(_REPO_ROOT, "logical_reimplementation_OF-RHM")
if _LOGICAL not in sys.path:
    sys.path.insert(0, _LOGICAL)

from controller.dead.emn import EntropyMixingNetwork


@dataclass
class Config:
    trials: int = 20
    bytes_per_trial: int = 65_536
    alpha: float = 0.05


def _bytes_from_emn(n_bytes: int) -> bytes:
    emn = EntropyMixingNetwork()
    out = bytearray()
    while len(out) < n_bytes:
        out.extend(emn.next())
    return bytes(out[:n_bytes])


def _bytes_from_system(n_bytes: int) -> bytes:
    return os.urandom(n_bytes)


def _to_bit_sequence(b: bytes):
    arr = np.frombuffer(b, dtype=np.uint8)
    return pack_sequence(arr)


def _run_nist_on_bytes(b: bytes):
    seq = _to_bit_sequence(b)
    eligible = check_eligibility_all_battery(seq, SP800_22R1A_BATTERY)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        results = run_all_battery(seq, eligible, SP800_22R1A_BATTERY)
    return {r.name: bool(r.passed) for r, _ in results}


def _aggregate(name: str, cfg: Config, gen_fn):
    per_test: dict[str, list[bool]] = {}
    durations: list[float] = []

    for i in range(cfg.trials):
        t0 = time.perf_counter()
        b = gen_fn(cfg.bytes_per_trial)
        outcomes = _run_nist_on_bytes(b)
        durations.append(time.perf_counter() - t0)

        for test_name, passed in outcomes.items():
            per_test.setdefault(test_name, []).append(passed)

        print(f"  {name}: trial {i+1:2d}/{cfg.trials} done")

    return per_test, durations


def _parse_args() -> Config:
    p = argparse.ArgumentParser(add_help=True)
    p.add_argument("--trials", type=int, default=Config.trials)
    p.add_argument("--bytes", dest="bytes_per_trial", type=int, default=Config.bytes_per_trial)
    p.add_argument("--alpha", type=float, default=Config.alpha)
    args = p.parse_args()
    return Config(trials=args.trials, bytes_per_trial=args.bytes_per_trial, alpha=args.alpha)


def main():
    cfg = _parse_args()
    print(f"NIST SP 800-22 Rev 1a (alpha={cfg.alpha})")
    print(f"Trials: {cfg.trials}")
    print(f"Bytes per trial: {cfg.bytes_per_trial:,} ({cfg.bytes_per_trial*8:,} bits)\n")

    print("[1/2] os.urandom")
    sys_results, sys_dur = _aggregate("os.urandom", cfg, _bytes_from_system)

    print("\n[2/2] EMN")
    emn_results, emn_dur = _aggregate("EMN", cfg, _bytes_from_emn)

    all_tests = sorted(set(sys_results.keys()) | set(emn_results.keys()))

    print("\n=== Pass counts (passes/trials) ===")
    print(f"{'Test':<40} {'os.urandom':>12} {'EMN':>12}")
    print("-" * 68)
    for t in all_tests:
        sp = sum(sys_results.get(t, []))
        ep = sum(emn_results.get(t, []))
        sn = len(sys_results.get(t, []))
        en = len(emn_results.get(t, []))
        s = f"{sp}/{sn}" if sn else "N/A"
        e = f"{ep}/{en}" if en else "N/A"
        print(f"{t:<40} {s:>12} {e:>12}")

    print("\n=== Runtime (seconds per trial, mean) ===")
    print(f"os.urandom: {sum(sys_dur)/len(sys_dur):.3f}s")
    print(f"EMN      : {sum(emn_dur)/len(emn_dur):.3f}s")


if __name__ == '__main__':
    main()
