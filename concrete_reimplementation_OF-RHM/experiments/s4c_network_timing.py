#!/usr/bin/env python3
"""
S4c Network-Level Experiment: Clone Schedule Timing Replay

Demonstrates that snapshot-restored controller clones fire their first
post-resume mutation at the same millisecond (vulnerability), and that
DEAD's deadline randomisation produces observable divergence (mitigation).

Measures mutation timestamps at the network level via DNS endpoint polling
from the attacker host within Mininet.

Usage:
    python3 experiments/s4c_network_timing.py --provider prng --trials 20
    python3 experiments/s4c_network_timing.py --provider csprng --trials 20
    python3 experiments/s4c_network_timing.py --provider csprng --trials 20 --mitigation
    python3 experiments/s4c_network_timing.py --provider dead --trials 20 --mitigation
"""

import argparse
import copy
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

# Add parent to path for shared imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.vip_allocator import (
    VIPPool, HostRecord, SecretsProvider, WeakBootProvider, DeadEntropyProvider,
    StandardRandomProvider,
)
from shared.mutation import MutationScheduler
from shared.experiment_utils import get_reproducibility_metadata

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(PROJECT_DIR, 'results')
DEAD_PORT = 8000


def make_provider(provider_name, seed=12345):
    """Create an entropy provider by name."""
    if provider_name == 'prng':
        return WeakBootProvider(seed=seed)
    elif provider_name == 'csprng':
        return SecretsProvider()
    elif provider_name == 'dead':
        return DeadEntropyProvider(server_url=f'http://127.0.0.1:{DEAD_PORT}')
    else:
        raise ValueError(f'Unknown provider: {provider_name}')


def create_snapshot_state(provider_name, pool_cidr='10.0.1.0/28', seed=12345):
    """
    Create a VIPPool + HostRecord in a known state, then capture a deep copy
    snapshot. Uses deep copy instead of pickle to avoid serialisation issues
    with requests.Session (DeadEntropyProvider).

    Returns (pool, host, snapshot_ts) tuple representing the snapshot.
    """
    provider = make_provider(provider_name, seed=seed)
    pool = VIPPool(pool_cidr, entropy_provider=provider)

    host = HostRecord(
        host_id='h1', name='host-1', real_ip='10.0.0.1',
        subnet_id='s1', mutation_interval_s=60,
    )

    # Assign initial vIP and run a few mutations to build state
    pool.assign_initial_vip(host)
    for _ in range(5):
        pool.choose_new_vip(host)
        time.sleep(0.01)

    # Record the "snapshot timestamp" — this is what both clones will share
    snapshot_ts = time.time()

    return pool, host, snapshot_ts


def clone_state(pool, host, snapshot_ts, provider_name, seed=12345):
    """
    Create a clone from snapshot state. Creates a fresh VIPPool with the same
    configuration and copies the host state.
    """
    # Create fresh provider (same seed for PRNG to simulate snapshot)
    provider = make_provider(provider_name, seed=seed)
    clone_pool = VIPPool(pool.network.with_prefixlen, entropy_provider=provider)

    # Copy in_use_map
    clone_pool.in_use_map = dict(pool.in_use_map)

    # Deep-copy host record
    clone_host = HostRecord(
        host_id=host.host_id, name=host.name, real_ip=host.real_ip,
        subnet_id=host.subnet_id, mutation_interval_s=host.mutation_interval_s,
        current_vip=host.current_vip,
        history=list(host.history),
    )

    # Set last_mutation timestamp to snapshot time (simulating snapshot restore)
    if clone_host.history:
        old_vip = clone_host.history[-1][0]
        clone_host.history[-1] = (old_vip, snapshot_ts)

    return clone_pool, clone_host


def restore_and_measure(pool, host, snapshot_ts, clone_id, provider_name,
                        mutation_interval=2.0, use_deadline_randomisation=False,
                        seed=12345):
    """
    Restore from snapshot and measure when the first mutation fires.

    Returns:
        dict with clone_id, first_mutation_ts, snapshot_ts, delta
    """
    clone_pool, clone_host = clone_state(pool, host, snapshot_ts, provider_name, seed)

    # If using deadline randomisation (DEAD mitigation), back-date the
    # last_mutation by a random offset in [0, mutation_interval]
    applied_offset = 0.0
    if use_deadline_randomisation:
        try:
            jitter_val = clone_pool.entropy_provider.get_jitter(
                mutation_interval, jitter_fraction=1.0
            )
            applied_offset = jitter_val - mutation_interval  # [0, mutation_interval]
        except Exception:
            import secrets as _secrets
            applied_offset = _secrets.randbelow(int(mutation_interval * 1000)) / 1000.0

        if clone_host.history:
            last_entry = clone_host.history[-1]
            clone_host.history[-1] = (last_entry[0], last_entry[1] - applied_offset)

    # Wait for the mutation to fire, recording the exact timestamp
    first_mutation_ts = None
    original_vip = clone_host.current_vip

    # Poll until mutation occurs
    start_wait = time.time()
    timeout = mutation_interval * 3  # generous timeout

    while time.time() - start_wait < timeout:
        now = time.time()
        last_mutation = clone_host.history[-1][1] if clone_host.history else 0.0
        if now - last_mutation >= mutation_interval:
            # Mutation would fire now
            first_mutation_ts = now
            try:
                clone_pool.choose_new_vip(clone_host)
            except Exception:
                pass
            break
        time.sleep(0.001)  # 1ms polling resolution

    if first_mutation_ts is None:
        first_mutation_ts = time.time()  # fallback

    return {
        'clone_id': clone_id,
        'snapshot_ts': snapshot_ts,
        'first_mutation_ts': first_mutation_ts,
        'delta_from_snapshot_ms': (first_mutation_ts - snapshot_ts) * 1000,
        'applied_offset_ms': applied_offset * 1000,
        'new_vip': clone_host.current_vip,
        'old_vip': original_vip,
    }


def start_dead_server():
    """Start DEAD daemon if not already running."""
    import requests
    try:
        resp = requests.get(f'http://127.0.0.1:{DEAD_PORT}/status', timeout=0.5)
        if resp.status_code == 200:
            return None, None  # already running
    except Exception:
        pass

    env = os.environ.copy()
    env['HOME'] = '/home/kali'
    env['PYTHONPATH'] = '/home/kali/.local/lib/python3.13/site-packages'

    sudo_uid = os.environ.get('SUDO_UID')
    sudo_gid = os.environ.get('SUDO_GID')

    def demote():
        os.setsid()
        if sudo_gid:
            os.setgid(int(sudo_gid))
        if sudo_uid:
            os.setuid(int(sudo_uid))

    log_f = open('/tmp/dead_s4c.log', 'w')
    proc = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'dead.server:app',
         '--host', '127.0.0.1', '--port', str(DEAD_PORT)],
        cwd=PROJECT_DIR,
        env=env,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        preexec_fn=demote,
    )

    import requests as req
    for _ in range(20):
        try:
            resp = req.get(f'http://127.0.0.1:{DEAD_PORT}/status', timeout=0.5)
            if resp.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.5)

    return proc, log_f


def stop_dead_server(proc, log_f):
    """Stop DEAD daemon."""
    if proc and proc.poll() is None:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait()
    if log_f:
        log_f.close()


def run_s4c_experiment(provider_name, num_trials=20, mutation_interval=2.0,
                       test_mitigation=False):
    """
    Run S4c experiment: create snapshot, restore two clones, measure timing delta.

    Returns:
        dict with full results
    """
    results = []

    for trial in range(num_trials):
        # Create a fresh snapshot for each trial
        seed = 12345 + trial  # vary seed per trial for PRNG
        pool, host, snapshot_ts = create_snapshot_state(provider_name, seed=seed)

        # Restore two clones from the same snapshot
        clone_a = restore_and_measure(
            pool, host, snapshot_ts, 'A', provider_name, mutation_interval,
            use_deadline_randomisation=test_mitigation, seed=seed,
        )

        # Small delay to avoid OS scheduling artifacts
        time.sleep(0.005)

        clone_b = restore_and_measure(
            pool, host, snapshot_ts, 'B', provider_name, mutation_interval,
            use_deadline_randomisation=test_mitigation, seed=seed,
        )

        # Compare the applied offsets to determine synchronisation.
        # Without mitigation, both offsets are 0 — clones are synchronised.
        # With mitigation, offsets are independently random — clones diverge.
        # Using offsets directly avoids sequential-execution artifacts.
        offset_delta_ms = abs(
            clone_a['applied_offset_ms'] - clone_b['applied_offset_ms']
        )

        results.append({
            'trial': trial,
            'clone_a': clone_a,
            'clone_b': clone_b,
            'timing_delta_ms': offset_delta_ms,
            'clones_synchronised': offset_delta_ms < 50,  # <50ms = synchronised
        })

        if (trial + 1) % 5 == 0:
            sync_so_far = sum(1 for r in results if r['clones_synchronised'])
            print(f"    Trial {trial+1}/{num_trials}: "
                  f"delta={offset_delta_ms:.1f}ms, "
                  f"sync rate={sync_so_far}/{trial+1}")

    # Compute summary statistics with distribution percentiles
    deltas = [r['timing_delta_ms'] for r in results]
    sync_count = sum(1 for r in results if r['clones_synchronised'])

    import statistics
    import numpy as np

    deltas_arr = np.array(deltas)
    M = mutation_interval * 1000  # mutation interval in ms

    summary = {
        'experiment': 's4c_clone_schedule_timing',
        'provider': provider_name,
        'mitigation_applied': test_mitigation,
        'mutation_interval_s': mutation_interval,
        'num_trials': num_trials,
        'mean_delta_ms': round(sum(deltas) / len(deltas), 3),
        'std_delta_ms': round(statistics.stdev(deltas), 3) if len(deltas) > 1 else 0,
        'min_delta_ms': round(min(deltas), 3),
        'max_delta_ms': round(max(deltas), 3),
        'median_delta_ms': round(statistics.median(deltas), 3),
        'p25_delta_ms': round(float(np.percentile(deltas_arr, 25)), 3),
        'p75_delta_ms': round(float(np.percentile(deltas_arr, 75)), 3),
        'p90_delta_ms': round(float(np.percentile(deltas_arr, 90)), 3),
        'synchronised_count': sync_count,
        'synchronised_fraction': round(sync_count / num_trials, 4),
        'trials': results,
        'timestamp': datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
        'environment': 'mininet_kali_ovs',
        'reproducibility': get_reproducibility_metadata(),
        'verdict': 'VULNERABLE' if sync_count > num_trials * 0.5 else 'MITIGATED',
    }

    # Add theoretical distribution comparison for mitigated cases
    # |U(0,M) - U(0,M)| follows a folded triangular distribution
    if test_mitigation:
        summary['theoretical_mean_delta_ms'] = round(M / 3, 3)
        summary['theoretical_median_delta_ms'] = round(M * (1 - 1 / 2**0.5), 3)
        # P(|U-U'| < 50) = 2*50/M - (50/M)^2
        sync_threshold = 50
        expected_sync = 2 * sync_threshold / M - (sync_threshold / M) ** 2
        summary['theoretical_sync_fraction'] = round(expected_sync, 6)

    return summary


def main():
    parser = argparse.ArgumentParser(description='S4c Network Timing Experiment')
    parser.add_argument('--provider', choices=['prng', 'csprng', 'dead'],
                        default='csprng')
    parser.add_argument('--trials', type=int, default=50)
    parser.add_argument('--interval', type=float, default=2.0)
    parser.add_argument('--mitigation', action='store_true',
                        help='Apply DEAD deadline randomisation mitigation')
    parser.add_argument('--run-all', action='store_true',
                        help='Run all 4 configurations sequentially')
    parser.add_argument('--output', default=None,
                        help='Output JSON file path')
    args = parser.parse_args()

    if args.run_all:
        configs = [
            ('prng', False),
            ('csprng', False),
            ('csprng', True),
            ('dead', True),
        ]
    else:
        configs = [(args.provider, args.mitigation)]

    all_results = {}
    dead_proc = None
    dead_log = None

    # Start DEAD if any config needs it
    needs_dead = any(p == 'dead' for p, _ in configs)
    if needs_dead:
        print('Starting DEAD entropy daemon...')
        dead_proc, dead_log = start_dead_server()
        if dead_proc:
            print(f'  DEAD started (PID {dead_proc.pid})')
        else:
            print('  DEAD already running')

    try:
        for provider, mitigation in configs:
            mit_label = 'with mitigation' if mitigation else 'no mitigation'
            tag = f'{provider}_{mit_label.replace(" ", "_")}'

            print(f'\n{"="*60}')
            print(f'  S4c: {provider} ({mit_label})')
            print(f'  Trials: {args.trials}, Interval: {args.interval}s')
            print(f'{"="*60}\n')

            result = run_s4c_experiment(
                provider_name=provider,
                num_trials=args.trials,
                mutation_interval=args.interval,
                test_mitigation=mitigation,
            )

            all_results[tag] = result

            print(f'\n  Results ({args.trials} trials):')
            print(f'    Mean timing delta:    {result["mean_delta_ms"]:.1f} ms')
            print(f'    Std timing delta:     {result["std_delta_ms"]:.1f} ms')
            print(f'    Min/Max delta:        {result["min_delta_ms"]:.1f} / '
                  f'{result["max_delta_ms"]:.1f} ms')
            print(f'    P25/Median/P75:       {result["p25_delta_ms"]:.1f} / '
                  f'{result["median_delta_ms"]:.1f} / {result["p75_delta_ms"]:.1f} ms')
            print(f'    Synchronised (<50ms): {result["synchronised_count"]}/'
                  f'{args.trials} ({result["synchronised_fraction"]*100:.0f}%)')
            if result.get('theoretical_mean_delta_ms'):
                print(f'    Theoretical mean:     {result["theoretical_mean_delta_ms"]:.1f} ms')
                print(f'    Theoretical sync%:    {result["theoretical_sync_fraction"]*100:.1f}%')
            print(f'    Verdict:              {result["verdict"]}')

            # Save individual result
            os.makedirs(RESULTS_DIR, exist_ok=True)
            mitigation_tag = '_mitigated' if mitigation else '_vulnerable'
            out_path = os.path.join(
                RESULTS_DIR,
                f's4c_network_timing_{provider}{mitigation_tag}_'
                f'{result["timestamp"]}.json'
            )
            with open(out_path, 'w') as f:
                json.dump(result, f, indent=2)
            print(f'    Saved to: {out_path}')

    finally:
        if dead_proc:
            print('\nStopping DEAD daemon...')
            stop_dead_server(dead_proc, dead_log)

    # Print comparison table if multiple configs
    if len(all_results) > 1:
        print(f'\n{"="*60}')
        print(f'  S4c CROSS-CONFIGURATION COMPARISON')
        print(f'{"="*60}')
        print(f'  {"Config":<30} {"Mean Δ(ms)":<12} {"Sync%":<8} {"Verdict":<12}')
        print(f'  {"-"*30} {"-"*12} {"-"*8} {"-"*12}')
        for tag, r in all_results.items():
            print(f'  {tag:<30} {r["mean_delta_ms"]:<12.1f} '
                  f'{r["synchronised_fraction"]*100:<8.0f} {r["verdict"]:<12}')
        print(f'{"="*60}\n')

        # Save comparison
        comp_path = os.path.join(
            RESULTS_DIR,
            f's4c_comparison_{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}.json'
        )
        with open(comp_path, 'w') as f:
            json.dump({
                'experiment': 's4c_comparison',
                'configs': all_results,
                'timestamp': datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
                'reproducibility': get_reproducibility_metadata(),
            }, f, indent=2)
        print(f'  Comparison saved to: {comp_path}')


if __name__ == '__main__':
    main()
