#!/usr/bin/env python3
"""
S2 Network-Level Collision Experiment for OF-RHM.

Measures cross-replica vIP collision rates using the simplified in-process
approach: multiple VIPPool instances within a single process, each with a
different replica_id, simulating independent controller replicas.

This validates the Birthday Paradox collision rates observed in the logical
implementation by running the same measurement using the shared VIPPool
component within the concrete Mininet environment context.

The S2 result is about address selection collisions, which are independent of
whether the collision happens in Python or in OpenFlow flow tables.

Usage:
    sudo python3 experiments/s2_network_collision.py
    sudo python3 experiments/s2_network_collision.py --steps 500 --replicas 5
    sudo python3 experiments/s2_network_collision.py --run-in-mininet
"""

import argparse
import collections
import json
import math
import os
import signal
import subprocess
import statistics
import sys
import time
from datetime import datetime, timezone

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.vip_allocator import (
    VIPPool, HostRecord,
    StandardRandomProvider, SecretsProvider, WeakBootProvider, DeadEntropyProvider,
)
from shared.experiment_utils import get_reproducibility_metadata, wilson_ci, birthday_bound

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(PROJECT_DIR, 'results')
DEAD_PORT = 8010


def start_dead_server(port):
    """Start DEAD entropy daemon, returning subprocess handle."""
    env = os.environ.copy()
    env['HOME'] = '/home/kali'
    env['PYTHONPATH'] = (
        '/home/kali/.local/lib/python3.13/site-packages:'
        + os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

    # Drop privileges if running under sudo
    sudo_uid = os.environ.get('SUDO_UID')
    sudo_gid = os.environ.get('SUDO_GID')

    def demote():
        os.setsid()
        if sudo_gid:
            os.setgid(int(sudo_gid))
        if sudo_uid:
            os.setuid(int(sudo_uid))

    log_path = f'/tmp/dead_s2_{port}_{os.getpid()}.log'
    log_f = open(log_path, 'w')
    proc = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'dead.server:app',
         '--host', '127.0.0.1', '--port', str(port)],
        cwd=PROJECT_DIR,
        env=env,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        preexec_fn=demote,
    )
    # Wait for server to become ready
    import requests
    for attempt in range(20):
        try:
            resp = requests.get(f'http://127.0.0.1:{port}/status', timeout=0.5)
            if resp.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        print(f"  WARNING: DEAD server on port {port} may not be ready")

    return proc, log_f


def stop_dead_server(proc, log_f):
    """Stop DEAD entropy daemon."""
    if proc and proc.poll() is None:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait()
    if log_f:
        log_f.close()


def run_condition(pool_cidr, num_replicas, num_steps, entropy_factory, label,
                  reserved_ips=None):
    """
    Run one S2 condition: create num_replicas independent VIPPools, each
    independently assigning vIPs at each step. Measure collision rate.

    Returns dict with results.
    """
    if reserved_ips is None:
        # Reserve 5 IPs to match logical implementation (effective pool = 9)
        reserved_ips = [f'10.0.1.{i}' for i in range(1, 6)]

    replicas = []
    for _ in range(num_replicas):
        pool = VIPPool(pool_cidr, entropy_provider=entropy_factory())
        for rip in reserved_ips:
            pool.in_use_map[rip] = 'reserved'
        replicas.append(pool)

    host_def = HostRecord('h1', 'host-1', '10.0.0.1', 's1', 60)
    effective_pool_size = len(replicas[0].available_vips) - len(reserved_ips)

    steps_with_collision = 0
    collision_counts = []  # per-step collision count
    all_choices = []

    for step in range(num_steps):
        step_choices = []
        for pool in replicas:
            # Clear previous assignment for this host
            for v, h_id in list(pool.in_use_map.items()):
                if h_id == host_def.host_id:
                    del pool.in_use_map[v]
            host_def.current_vip = None
            vip = pool.assign_initial_vip(host_def)
            step_choices.append(vip)

        counts = collections.Counter(step_choices)
        num_colliding = sum(1 for c in counts.values() if c > 1)
        if num_colliding > 0:
            steps_with_collision += 1
        collision_counts.append(num_colliding)
        all_choices.extend(step_choices)

        if (step + 1) % 100 == 0:
            rate_so_far = steps_with_collision / (step + 1)
            print(f"    Step {step+1}/{num_steps}: collision rate so far = {rate_so_far*100:.1f}%")

    collision_rate = steps_with_collision / num_steps
    ci_low, ci_high = wilson_ci(collision_rate, num_steps)
    theoretical = birthday_bound(num_replicas, effective_pool_size)

    # Address distribution analysis with chi-squared p-value
    from scipy.stats import chi2

    addr_counts = collections.Counter(all_choices)
    addr_freqs = list(addr_counts.values())
    expected_count = num_steps * num_replicas / effective_pool_size
    chi_squared = sum(
        (obs - expected_count) ** 2 / expected_count
        for obs in addr_freqs
    ) if addr_freqs else 0
    chi2_df = effective_pool_size - 1
    chi2_p_value = float(chi2.sf(chi_squared, chi2_df))

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  Config: k={num_replicas} replicas, n={effective_pool_size} candidates")
    print(f"  Steps: {num_steps}")
    print(f"  Collisions: {steps_with_collision}/{num_steps}")
    print(f"  Collision rate: {collision_rate*100:.1f}%")
    print(f"  95% Wilson CI: [{ci_low*100:.1f}%, {ci_high*100:.1f}%]")
    print(f"  Theoretical Birthday bound: {theoretical*100:.1f}%")
    print(f"  Within CI: {'YES' if ci_low <= theoretical <= ci_high else 'NO'}")
    print(f"  Unique addresses used: {len(addr_counts)}/{effective_pool_size}")
    print(f"  Chi-squared (uniformity): {chi_squared:.2f} (df={chi2_df}, p={chi2_p_value:.4f})")
    print(f"{'='*60}\n")

    return {
        'label': label,
        'num_replicas': num_replicas,
        'effective_pool_size': effective_pool_size,
        'num_steps': num_steps,
        'steps_with_collision': steps_with_collision,
        'collision_rate': round(collision_rate, 6),
        'ci_95_low': round(ci_low, 6),
        'ci_95_high': round(ci_high, 6),
        'theoretical_birthday_bound': round(theoretical, 6),
        'within_ci': ci_low <= theoretical <= ci_high,
        'unique_addresses_used': len(addr_counts),
        'chi_squared_uniformity': round(chi_squared, 4),
        'chi2_df': chi2_df,
        'chi2_p_value': round(chi2_p_value, 6),
        'mean_collisions_per_step': round(statistics.mean(collision_counts), 4),
    }


def s2_experiment_mininet(net, topo_args):
    """
    Run S2 collision experiment within Mininet context.
    The experiment itself is in-process (VIPPool instances), but runs
    inside the Mininet topology to validate the shared component behavior
    in the concrete network environment.
    """
    pool_cidr = topo_args['pool_cidr']
    num_replicas = topo_args.get('num_replicas', 5)
    num_steps = topo_args.get('num_steps', 500)

    print(f"\n{'#'*60}")
    print(f"  S2 NETWORK-LEVEL COLLISION EXPERIMENT (in Mininet)")
    print(f"{'#'*60}\n")

    _run_all_conditions(pool_cidr, num_replicas, num_steps, context='mininet')


def _run_all_conditions(pool_cidr, num_replicas, num_steps, context='standalone'):
    """Run all S2 conditions and save results."""
    all_results = {}

    # --- Primary configuration: k=5, n=9 ---
    print(f"\n{'#'*60}")
    print(f"  Configuration 1: k={num_replicas} replicas")
    print(f"{'#'*60}")

    # Condition 1: PRNG (WeakBootProvider / StandardRandomProvider)
    print(f"\n--- Condition 1: PRNG (StandardRandomProvider) ---")
    all_results['prng'] = run_condition(
        pool_cidr, num_replicas, num_steps,
        StandardRandomProvider,
        f'PRNG k={num_replicas}',
    )

    # Condition 2: CSPRNG (SecretsProvider)
    print(f"\n--- Condition 2: CSPRNG (SecretsProvider) ---")
    all_results['csprng'] = run_condition(
        pool_cidr, num_replicas, num_steps,
        SecretsProvider,
        f'CSPRNG k={num_replicas}',
    )

    # Condition 3: DEAD v1.0 (entropy only, no coordination)
    print(f"\n--- Condition 3: DEAD (entropy only, no coordination) ---")
    dead_proc, dead_log = start_dead_server(DEAD_PORT)
    dead_url = f'http://127.0.0.1:{DEAD_PORT}'
    try:
        all_results['dead'] = run_condition(
            pool_cidr, num_replicas, num_steps,
            lambda: DeadEntropyProvider(server_url=dead_url),
            f'DEAD k={num_replicas}',
        )
    except Exception as e:
        print(f"  DEAD condition failed: {e}")
    finally:
        stop_dead_server(dead_proc, dead_log)

    # --- Second configuration: k=3 (same pool) for Birthday bound validation ---
    k2 = 3
    print(f"\n{'#'*60}")
    print(f"  Configuration 2: k={k2} replicas (same pool)")
    print(f"{'#'*60}")

    print(f"\n--- Condition 4: PRNG k={k2} ---")
    all_results[f'prng_k{k2}'] = run_condition(
        pool_cidr, k2, num_steps,
        StandardRandomProvider,
        f'PRNG k={k2}',
    )

    print(f"\n--- Condition 5: CSPRNG k={k2} ---")
    all_results[f'csprng_k{k2}'] = run_condition(
        pool_cidr, k2, num_steps,
        SecretsProvider,
        f'CSPRNG k={k2}',
    )

    print(f"\n--- Condition 6: DEAD k={k2} ---")
    dead_proc, dead_log = start_dead_server(DEAD_PORT)
    try:
        all_results[f'dead_k{k2}'] = run_condition(
            pool_cidr, k2, num_steps,
            lambda: DeadEntropyProvider(server_url=dead_url),
            f'DEAD k={k2}',
        )
    except Exception as e:
        print(f"  DEAD k={k2} condition failed: {e}")
    finally:
        stop_dead_server(dead_proc, dead_log)

    # --- Cross-condition comparison ---
    print(f"\n{'='*60}")
    print(f"  CROSS-PROVIDER S2 COLLISION COMPARISON")
    print(f"{'='*60}")
    print(f"  {'Config':<16} {'Collision%':<12} {'95% CI':<22} {'Theory%':<10} {'Match?':<8} {'Chi2 p':<8}")
    print(f"  {'-'*16} {'-'*12} {'-'*22} {'-'*10} {'-'*8} {'-'*8}")
    for key, r in all_results.items():
        ci_str = f"[{r['ci_95_low']*100:.1f}%, {r['ci_95_high']*100:.1f}%]"
        print(f"  {key:<16} {r['collision_rate']*100:<12.1f} {ci_str:<22} "
              f"{r['theoretical_birthday_bound']*100:<10.1f} "
              f"{'YES' if r['within_ci'] else 'NO':<8} "
              f"{r['chi2_p_value']:<8.4f}")
    print(f"{'='*60}\n")

    # --- Save results ---
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = {
        'experiment': 's2_network_collision',
        'pool_cidr': pool_cidr,
        'configurations': {
            f'k{num_replicas}': {
                'num_replicas': num_replicas,
                'conditions': {k: v for k, v in all_results.items()
                               if not k.endswith(f'_k{k2}')},
            },
            f'k{k2}': {
                'num_replicas': k2,
                'conditions': {k: v for k, v in all_results.items()
                               if k.endswith(f'_k{k2}')},
            },
        },
        'num_steps': num_steps,
        'timestamp': timestamp,
        'environment': 'mininet_kali_ovs' if context == 'mininet' else 'standalone_kali',
        'reproducibility': get_reproducibility_metadata(),
    }

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, f's2_network_collision_{timestamp}.json')
    with open(out_path, 'w') as f:
        json.dump(output, f, indent=2)
    print(f"  Results saved to: {out_path}\n")

    return all_results


def main():
    parser = argparse.ArgumentParser(description='S2 Network-Level Collision Experiment')
    parser.add_argument('--replicas', type=int, default=5,
                        help='Number of simulated replicas (default: 5)')
    parser.add_argument('--steps', type=int, default=500,
                        help='Number of mutation steps to simulate (default: 500)')
    parser.add_argument('--pool-cidr', type=str, default='10.0.1.0/28',
                        help='VIP pool CIDR (default: 10.0.1.0/28)')
    parser.add_argument('--run-in-mininet', action='store_true',
                        help='Run inside Mininet topology (requires sudo)')
    args = parser.parse_args()

    if args.run_in_mininet:
        from topology.ofrhm_topo import run as run_topo

        def experiment_fn(net, topo_args):
            topo_args['num_replicas'] = args.replicas
            topo_args['num_steps'] = args.steps
            s2_experiment_mininet(net, topo_args)

        run_topo(
            n=1,
            pool_cidr=args.pool_cidr,
            mutation_interval=2.0,
            entropy_provider='csprng',
            experiment_fn=experiment_fn,
        )
    else:
        print(f"\n{'#'*60}")
        print(f"  S2 COLLISION EXPERIMENT (standalone)")
        print(f"  Replicas: {args.replicas}, Steps: {args.steps}")
        print(f"  Pool: {args.pool_cidr}")
        print(f"{'#'*60}\n")
        _run_all_conditions(args.pool_cidr, args.replicas, args.steps,
                            context='standalone')


if __name__ == '__main__':
    main()
