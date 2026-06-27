# eval_battery.py 
"""
Statistical evaluation battery using numpy/scipy/statsmodels.
"""
import time
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats, signal
import statsmodels.api as sm
from statsmodels.sandbox.stats import runs as runs_mod
from statsmodels.tsa import stattools
import math
import os
import json
import statistics

from .emn import EntropyMixingNetwork
from .reseeder import Reseeder

# ------ Parameters ------ 
SAMPLES = int(5e6) # Original was 1e6
BYTE_CHUNK = 32
TRIALS = 20  # Increased from 1 to 20 for the ESORICS 2026 evaluation
NLAGS = 1000
NPERSEG = 4096
OUTDIR = "batter_eval_out"
os.makedirs(OUTDIR, exist_ok=True)

# ------ Generation ------ 
def bytes_from_emn(emn, n_bytes):
    out = bytearray()
    while len(out) < n_bytes:
        out.extend(emn.next())
    return bytes(out[:n_bytes])

def bytes_from_system(n_bytes, seed=12345):
    import random
    rng = random.Random(seed)
    out = bytearray()
    while len(out) < n_bytes:
        v = rng.getrandbits(BYTE_CHUNK*8)
        out.extend(v.to_bytes(BYTE_CHUNK, "big"))
    return bytes(out[:n_bytes])

# DIRECTLY DRAWS FROM OS INSTEAD OF MERSENNE TWISTER
def bytes_from_system_pure(n_bytes):
    return os.urandom(n_bytes)

# Convert raw byte stream to empirical distribution
def pmf_from_bytes(b):
    counts = np.bincount(np.frombuffer(b, dtype=np.uint8), minlength=256)
    probs = counts / counts.sum()
    return probs, counts

def gen_emn_wrapper(n_bytes, deterministic_seed=None):
    emn = EntropyMixingNetwork(initial_seed=deterministic_seed) if deterministic_seed is not None else EntropyMixingNetwork()
    t0 = time.perf_counter()
    data = bytes_from_emn(emn, n_bytes)
    t1 = time.perf_counter()
    return data, t1 - t0

def gen_emn_with_reseeder(n_bytes, deterministic_seed=None, period=0.05, jitter_frac=0.2):
    emn = EntropyMixingNetwork(initial_seed=deterministic_seed, paper_mode_xor=True) if deterministic_seed else EntropyMixingNetwork(paper_mode_xor=True)
    reseeder = Reseeder(
        emn,
        periodic_seconds=period,
        jitter_frac=jitter_frac,
        min_interval_seconds=0.10
    )
    t0 = time.perf_counter()
    reseeder.start_periodic()
    try:
        data = bytes_from_emn(emn, n_bytes)
    finally:
        reseeder.close()
    t1 = time.perf_counter()
    return data, t1 - t0

def gen_sys_wrapper(n_bytes, seed=12345):
    t0 = time.perf_counter()
    #data = bytes_from_system(n_bytes, seed=seed)
    data = bytes_from_system_pure(n_bytes)
    t1 = time.perf_counter()
    return data, t1 - t0

# ------ Statistical Tests ------
def chi_square_test(byte_stream):
    probs, counts = pmf_from_bytes(byte_stream)
    expected = np.full(counts.shape, counts.sum() / 256.0, dtype=float)
    chi2, p = stats.chisquare(counts, expected)
    return float(chi2), float(p), counts

def shannon_and_min_entropy(byte_stream):
    probs, _ = pmf_from_bytes(byte_stream)
    probs_pos = probs[probs > 0]
    H = float(stats.entropy(probs_pos, base=2))
    Hmin = -math.log2(probs.max()) if probs.max() > 0 else float("inf")
    return H, Hmin

def runs_test_bits(byte_stream):
    bits = np.unpackbits(np.frombuffer(byte_stream, dtype=np.uint8))
    zstat, pvalue = runs_mod.runstest_1samp(bits)
    return float(zstat), float(pvalue)

def lag1_predictability(byte_stream):
    vals = np.frombuffer(byte_stream, dtype=np.uint8).astype(np.float64)
    if len(vals) < 2:
        return 0.0
    return float(np.corrcoef(vals[:-1], vals[1:])[0, 1])

def psd_welch(byte_stream, fs=1.0, nperseg=NPERSEG):
    vals = np.frombuffer(byte_stream, dtype=np.uint8).astype(np.float64)
    f, Pxx = signal.welch(vals - vals.mean(), fs=fs, nperseg=nperseg)
    return f, Pxx

def lag_heatmap(byte_stream, window=256, rows=64):
    arr = np.frombuffer(byte_stream, dtype=np.uint8) [:window*rows]
    if len(arr) < window*rows:
        arr = np.pad(arr, (0, window*rows - len(arr)), 'wrap')
    mat = arr.reshape((rows, window))
    return np.corrcoef(mat)

def acf_test(byte_stream, nlags=1000, fft=True):
    vals = np.frombuffer(byte_stream, dtype=np.uint8).astype(np.float64)
    if vals.size == 0:
        return np.array([])
    vals = vals - vals.mean()

    effective_nlags = min(nlags, max(0, vals.size - 1))
    acf_vals = stattools.acf(vals, nlags=effective_nlags, fft=fft)

    if effective_nlags < nlags:
        pad = _np.full((nlags - effective_nlags,), _np.nan)
        acf_vals = _np.concatenate([acf_vals, pad])

    return acf_vals

# ------ Orchestrate Battery ------

def run_trial(generator_fn, n_bytes):
    data, gen_time = generator_fn(n_bytes)
    results = {}
    results['gen_time_s'] = gen_time

    chi2, p, counts = chi_square_test(data)
    results.update({'chi2': chi2, 'chi2_p': p})

    H, Hmin = shannon_and_min_entropy(data)
    results.update({'shannon_H': H, 'min_H': Hmin})

    z_runs, p_runs = runs_test_bits(data)
    results.update({'runs_z':z_runs, 'runs_p': p_runs})

    results['lag1_corr'] = lag1_predictability(data)
    acf_vals = acf_test(data, nlags=200)

    results['acf_first5'] = acf_vals[:5].tolist()
    f, Pxx = psd_welch(data)

    return results, data, counts, (f, Pxx)

def run_battery(n_bytes=SAMPLES, trials=TRIALS):
    records = []
    scalar_metrics = ['gen_time_s', 'chi2', 'chi2_p', 'shannon_H', 'min_H', 'runs_z', 'runs_p', 'lag1_corr']

    # trial_results[variant][metric] = list of per-trial scalar values
    trial_results = {v: {m: [] for m in scalar_metrics} for v in ["SystemRandom", "EMN"]}

    # Baseline: SystemRandom
    for i in range(trials):
        res, data, counts, psd = run_trial(lambda n: gen_sys_wrapper(n, seed=12345 + i), n_bytes)
        res.update({'variant': 'SystemRandom', 'trial': i})
        records.append(res)
        open(os.path.join(OUTDIR, f"sys_trial{i}.bin"), "wb").write(data)
        for m in scalar_metrics:
            trial_results["SystemRandom"][m].append(res[m])

    # EMN: with reseeder
    # Change EMN Wrapper Function to add to remove reseeding module
    for i in range(trials):
        res, data, counts, psd = run_trial(lambda n: gen_emn_with_reseeder(n, deterministic_seed=None), n_bytes)
        res.update({'variant': 'EMN', 'trial': i})
        records.append(res)
        open(os.path.join(OUTDIR, f"emn_trial{i}.bin"), "wb").write(data)
        for m in scalar_metrics:
            trial_results["EMN"][m].append(res[m])

    # --- Multi-trial aggregation summary ---
    for variant_label in ["SystemRandom", "EMN"]:
        print(f"\n=== Multi-Trial Summary: {variant_label} ===")
        print(f"Trials: {TRIALS} x {SAMPLES} bytes each")
        print(f"{'Metric':<30} {'Mean':>10} {'Std':>10} {'Min':>10} {'Max':>10}")
        print("-" * 65)
        for metric_name, values in trial_results[variant_label].items():
            mean = statistics.mean(values)
            std  = statistics.stdev(values) if len(values) > 1 else 0.0
            print(f"{metric_name:<30} {mean:>10.4f} {std:>10.4f} "
                  f"{min(values):>10.4f} {max(values):>10.4f}")

    # --- Runs test PASS/FAIL diagnostic (EMN) ---
    runs_pvals = trial_results["EMN"]["runs_p"]
    n_fail = sum(1 for p in runs_pvals if p < 0.05)
    print(f"\n=== Runs Test Diagnostic (EMN) ===")
    print(f"Trials with p < 0.05 (rejection): {n_fail}/{TRIALS}")
    print(f"Runs test p-values: {[round(p, 3) for p in runs_pvals]}")
    if n_fail == 0:
        print("PASS: No trial rejected randomness at alpha=0.05")
    elif n_fail <= 1:
        print("MARGINAL: 1 trial rejected — consistent with false "
              "positive rate at alpha=0.05")
    else:
        print(f"WARN: {n_fail} trials rejected — warrants honest "
              "discussion in paper Section VI.D")

    # --- CSV outputs ---
    df = pd.DataFrame.from_records(records)
    df.to_csv(os.path.join(OUTDIR, "summary.csv"), index=False)
    print("Saved summary to", os.path.join(OUTDIR, "summary.csv"))

    # Long-format per-trial CSV for supplementary material
    long_rows = []
    for rec in records:
        for m in scalar_metrics:
            long_rows.append({
                'trial':   rec['trial'],
                'variant': rec['variant'],
                'metric':  m,
                'value':   rec[m],
            })
    pd.DataFrame(long_rows).to_csv(
        os.path.join(OUTDIR, "summary_multitrail.csv"), index=False
    )
    print("Saved per-trial data to", os.path.join(OUTDIR, "summary_multitrail.csv"))

    return df

df = run_battery()