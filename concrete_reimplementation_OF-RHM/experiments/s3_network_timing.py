#!/usr/bin/env python3
"""
S3 Network-Level Timing Experiment for OF-RHM.

Measures inter-mutation intervals as observed from the network layer by
polling the DNS resolution endpoint from the external client host.

This validates that the timing predictability/unpredictability observed at
the Python level (logical implementation) manifests identically at the
network level (concrete Mininet/OVS implementation).

Usage:
    sudo python3 experiments/s3_network_timing.py --provider csprng --mutations 50
    sudo python3 experiments/s3_network_timing.py --provider prng --mutations 50
    sudo python3 experiments/s3_network_timing.py --provider dead --mutations 50
"""

import argparse
import json
import os
import signal
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from topology.ofrhm_topo import run
from shared.experiment_utils import get_reproducibility_metadata

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(PROJECT_DIR, 'results')


RYU_MANAGER = '/home/kali/.local/bin/ryu-manager'


def _start_controller(provider, mutation_interval, dead_url='http://127.0.0.1:8000',
                      use_jitter=True):
    """Start ryu-manager with the given entropy provider. Returns subprocess.Popen."""
    env = os.environ.copy()
    # Ensure the user's Python site-packages are visible even under sudo
    env['HOME'] = '/home/kali'
    env['PYTHONPATH'] = '/home/kali/.local/lib/python3.13/site-packages'
    user_path = '/home/kali/.local/bin:/usr/local/bin:/usr/bin:/bin'
    env['PATH'] = user_path + ':' + env.get('PATH', '')
    env['OFRHM_ENTROPY_PROVIDER'] = provider
    env['OFRHM_MUTATION_INTERVAL'] = str(mutation_interval)
    env['OFRHM_REUSE_TIMEOUT'] = '10'
    env['OFRHM_HOSTS_COUNT'] = '1'  # single host for cleaner measurement
    env['OFRHM_DEAD_URL'] = dead_url
    env['OFRHM_USE_JITTER'] = '1' if use_jitter else '0'

    log_path = f'/tmp/ryu_s3_{provider}.log'
    log_f = open(log_path, 'w')

    # When running under sudo, drop back to the real user for the controller
    # (os-ken is installed in the user's site-packages, not root's)
    sudo_uid = os.environ.get('SUDO_UID')
    sudo_gid = os.environ.get('SUDO_GID')

    def demote():
        """Pre-exec: drop root privileges back to the invoking user."""
        os.setsid()
        if sudo_gid:
            os.setgid(int(sudo_gid))
        if sudo_uid:
            os.setuid(int(sudo_uid))

    proc = subprocess.Popen(
        [RYU_MANAGER, 'controller/ofrhm_app.py'],
        cwd=PROJECT_DIR,
        env=env,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        preexec_fn=demote,
    )
    return proc, log_f


def _stop_controller(proc, log_f):
    """Stop the ryu-manager process."""
    if proc and proc.poll() is None:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait()
    if log_f:
        log_f.close()


def s3_experiment(net, topo_args):
    """
    Experiment callback: poll DNS endpoint from client, record mutation timestamps.
    """
    provider = topo_args['entropy_provider']
    condition_name = topo_args.get('condition_name', provider)
    condition_label = topo_args.get('condition_label', provider)
    num_mutations = topo_args.get('num_mutations', 100)
    mutation_interval = topo_args['mutation_interval']
    poll_interval = 0.025  # 25ms polling (reduces discretisation noise floor from ~29ms σ to ~7ms σ)

    client = net.get('client')
    gw = net.get('gw')

    print(f"\n{'='*60}")
    print(f"  S3 NETWORK-LEVEL TIMING EXPERIMENT")
    print(f"  Condition: {condition_label}")
    print(f"  Provider: {provider}")
    print(f"  Mutation interval: {mutation_interval}s")
    print(f"  Target mutations: {num_mutations}")
    print(f"{'='*60}\n")

    # Wait for controller to connect and switches to stabilize
    print("--- Waiting for controller connection (5s) ---")
    time.sleep(5)

    # Trigger ARP learning so DNS queries work
    print("--- Triggering ARP learning ---")
    gw.cmd('ping -c 1 -W 1 192.168.1.10')
    time.sleep(1)

    # Verify DNS endpoint is reachable
    print("--- Verifying DNS endpoint ---")
    for attempt in range(10):
        dns_check = client.cmd(
            'curl -s --max-time 2 http://192.168.1.1:8080/dns_resolve?name=host-1'
        )
        try:
            data = json.loads(dns_check)
            if data.get('vip'):
                print(f"    DNS OK: host-1 -> {data['vip']}")
                break
        except (json.JSONDecodeError, ValueError):
            pass
        time.sleep(1)
    else:
        print("    FATAL: DNS endpoint not reachable after 10 attempts")
        return

    # --- Main measurement loop ---
    print(f"\n--- Collecting {num_mutations} mutation events ---")
    mutation_timestamps = []
    last_vip = None
    polls = 0
    start_time = time.time()

    while len(mutation_timestamps) < num_mutations:
        resp = client.cmd(
            'curl -s --max-time 0.5 http://192.168.1.1:8080/dns_resolve?name=host-1'
        )
        polls += 1
        try:
            vip = json.loads(resp).get('vip')
        except (json.JSONDecodeError, ValueError):
            time.sleep(poll_interval)
            continue

        if vip != last_vip:
            now = time.time()
            if last_vip is not None:
                mutation_timestamps.append(now)
                n = len(mutation_timestamps)
                if n % 10 == 0 or n == num_mutations:
                    elapsed = now - start_time
                    print(f"    Mutation {n}/{num_mutations} "
                          f"(vIP: {last_vip} -> {vip}, elapsed: {elapsed:.1f}s)")
            last_vip = vip

        time.sleep(poll_interval)

    total_time = time.time() - start_time
    print(f"\n--- Collection complete: {len(mutation_timestamps)} mutations "
          f"in {total_time:.1f}s ({polls} polls) ---")

    # --- Compute inter-mutation intervals ---
    intervals = []
    for i in range(1, len(mutation_timestamps)):
        intervals.append(mutation_timestamps[i] - mutation_timestamps[i - 1])

    if not intervals:
        print("ERROR: Not enough mutations to compute intervals")
        return

    mean_interval = statistics.mean(intervals)
    std_interval = statistics.stdev(intervals) if len(intervals) > 1 else 0.0
    min_interval = min(intervals)
    max_interval = max(intervals)
    median_interval = statistics.median(intervals)

    print(f"\n{'='*60}")
    print(f"  RESULTS: {condition_label}")
    print(f"{'='*60}")
    print(f"  Mutations observed:    {len(mutation_timestamps)}")
    print(f"  Inter-mutation intervals (n={len(intervals)}):")
    print(f"    Mean:    {mean_interval:.4f}s")
    print(f"    Std:     {std_interval:.4f}s  ({std_interval*1000:.1f}ms)")
    print(f"    Min:     {min_interval:.4f}s")
    print(f"    Max:     {max_interval:.4f}s")
    print(f"    Median:  {median_interval:.4f}s")
    print(f"{'='*60}\n")

    # --- Save results ---
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    result = {
        'experiment': 's3_network_timing',
        'condition_name': condition_name,
        'condition_label': condition_label,
        'provider': provider,
        'mutation_interval_s': mutation_interval,
        'num_mutations': len(mutation_timestamps),
        'num_intervals': len(intervals),
        'intervals': [round(x, 6) for x in intervals],
        'mean_interval_s': round(mean_interval, 6),
        'std_interval_s': round(std_interval, 6),
        'std_ms': round(std_interval * 1000, 2),
        'min_interval_s': round(min_interval, 6),
        'max_interval_s': round(max_interval, 6),
        'median_interval_s': round(median_interval, 6),
        'total_time_s': round(total_time, 2),
        'poll_interval_s': poll_interval,
        'total_polls': polls,
        'timestamp': timestamp,
        'environment': 'mininet_kali_ovs',
        'reproducibility': get_reproducibility_metadata(),
    }

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, f's3_network_timing_{condition_name}_{timestamp}.json')
    with open(out_path, 'w') as f:
        json.dump(result, f, indent=2)
    print(f"  Results saved to: {out_path}\n")

    return result


def main():
    parser = argparse.ArgumentParser(description='S3 Network-Level Timing Experiment')
    parser.add_argument('--provider', type=str, default='csprng',
                        choices=['prng', 'csprng', 'dead'],
                        help='Entropy provider (default: csprng)')
    parser.add_argument('--use-jitter', action='store_true', default=True,
                        help='Enable scheduler jitter (default: True). '
                             'Pass --no-jitter for fixed-interval S3 baseline.')
    parser.add_argument('--no-jitter', dest='use_jitter', action='store_false',
                        help='Disable scheduler jitter — runs fixed-interval S3 '
                             'vulnerable baseline.')
    parser.add_argument('--run-all-conditions', action='store_true',
                        help='Run all four S3 conditions (fixed + jitter baselines)')
    parser.add_argument('--mutations', type=int, default=100,
                        help='Number of mutations to observe (default: 50)')
    parser.add_argument('--interval', type=float, default=2.0,
                        help='Mutation interval in seconds (default: 2.0)')
    parser.add_argument('--run-all', action='store_true',
                        help='Run experiment for all three providers sequentially')
    args = parser.parse_args()

    # --- Build condition list ---
    if args.run_all_conditions:
        # Four conditions that together reproduce the S3 failure and its mitigation.
        # Conditions A and B are the critical pair: same timing variance despite different
        # entropy quality — the S3 claim. Conditions C and D are the mitigated state.
        conditions = [
            {'name': 'prng_fixed',   'provider': 'prng',   'use_jitter': False,
             'label': 'PRNG (fixed interval)'},
            {'name': 'csprng_fixed', 'provider': 'csprng', 'use_jitter': False,
             'label': 'CSPRNG (fixed interval)'},
            {'name': 'dead_jitter',  'provider': 'dead',   'use_jitter': True,
             'label': 'DEAD (jitter)'},
            {'name': 'prng_jitter',  'provider': 'prng',   'use_jitter': True,
             'label': 'PRNG (jitter)'},
        ]
    elif args.run_all:
        # Legacy mode: all three providers with current jitter setting
        conditions = [
            {'name': f'{p}_{"jitter" if args.use_jitter else "fixed"}',
             'provider': p, 'use_jitter': args.use_jitter,
             'label': f'{p.upper()} ({"jitter" if args.use_jitter else "fixed interval"})'}
            for p in ['prng', 'csprng', 'dead']
        ]
    else:
        suffix = 'jitter' if args.use_jitter else 'fixed'
        conditions = [
            {'name': f'{args.provider}_{suffix}',
             'provider': args.provider, 'use_jitter': args.use_jitter,
             'label': f'{args.provider.upper()} ({"jitter" if args.use_jitter else "fixed interval"})'},
        ]

    all_results = {}

    for condition in conditions:
        provider_name = condition['provider']
        use_jitter = condition['use_jitter']
        cond_name = condition['name']
        label = condition['label']

        print(f"\n{'#'*60}")
        print(f"  STARTING S3 EXPERIMENT: {label}")
        print(f"{'#'*60}\n")

        # Start DEAD daemon if needed (in host namespace, as kali user)
        dead_proc = None
        dead_log_f = None
        if provider_name == 'dead':
            print("  Starting DEAD entropy daemon...")
            dead_log_f = open('/tmp/dead_s3.log', 'w')
            dead_env = os.environ.copy()
            dead_env['HOME'] = '/home/kali'
            dead_env['PYTHONPATH'] = '/home/kali/.local/lib/python3.13/site-packages'
            sudo_uid = os.environ.get('SUDO_UID')
            sudo_gid = os.environ.get('SUDO_GID')

            def demote_dead():
                os.setsid()
                if sudo_gid:
                    os.setgid(int(sudo_gid))
                if sudo_uid:
                    os.setuid(int(sudo_uid))

            dead_proc = subprocess.Popen(
                [sys.executable, '-m', 'uvicorn', 'dead.server:app',
                 '--host', '127.0.0.1', '--port', '8000'],
                cwd=PROJECT_DIR,
                env=dead_env,
                stdout=dead_log_f,
                stderr=subprocess.STDOUT,
                preexec_fn=demote_dead,
            )
            print(f"  DEAD daemon started (PID {dead_proc.pid})")
            time.sleep(3)

        # Start controller for this condition
        controller_proc, controller_log = _start_controller(
            provider_name, args.interval, use_jitter=use_jitter,
        )
        print(f"  Controller started (PID {controller_proc.pid}, "
              f"use_jitter={use_jitter})")
        time.sleep(3)  # let controller initialize

        # Check controller is alive
        if controller_proc.poll() is not None:
            print(f"  FATAL: Controller exited with code {controller_proc.returncode}")
            controller_log.close()
            if dead_proc:
                _stop_controller(dead_proc, dead_log_f)
            continue

        try:
            # Wrap s3_experiment to capture result
            captured = {}

            def experiment_fn(net, topo_args):
                topo_args['num_mutations'] = args.mutations
                topo_args['condition_name'] = cond_name
                topo_args['condition_label'] = label
                captured['result'] = s3_experiment(net, topo_args)

            run(
                n=1,  # single host for cleaner measurement
                pool_cidr='10.0.1.0/28',
                mutation_interval=args.interval,
                entropy_provider=provider_name,
                experiment_fn=experiment_fn,
            )

            if 'result' in captured:
                all_results[cond_name] = captured['result']

        finally:
            print(f"  Stopping controller (PID {controller_proc.pid})...")
            _stop_controller(controller_proc, controller_log)
            if dead_proc:
                print(f"  Stopping DEAD daemon (PID {dead_proc.pid})...")
                _stop_controller(dead_proc, dead_log_f)
            # Clean up Mininet state
            subprocess.run(['mn', '-c'], capture_output=True)
            time.sleep(2)

    # --- Summary across conditions ---
    if len(all_results) > 1:
        print(f"\n{'='*60}")
        print(f"  CROSS-CONDITION COMPARISON")
        print(f"{'='*60}")
        print(f"  {'Condition':<18} {'Mean (s)':<12} {'Std (ms)':<12} {'Min (s)':<12} {'Max (s)':<12}")
        print(f"  {'-'*18} {'-'*12} {'-'*12} {'-'*12} {'-'*12}")
        for cname, r in all_results.items():
            print(f"  {cname:<18} {r['mean_interval_s']:<12.4f} "
                  f"{r['std_ms']:<12.1f} "
                  f"{r['min_interval_s']:<12.4f} {r['max_interval_s']:<12.4f}")
        print(f"{'='*60}\n")

        # --- KS tests: three scientifically meaningful pairs ---
        from scipy.stats import ks_2samp

        # Three scientifically meaningful pairs.
        # Pair 1: entropy quality is irrelevant to timing (S3 claim).
        #   Both conditions are fixed-interval; if p > 0.05, the distributions are the same.
        # Pair 2 & 3: mitigation is detectable from the network (DEAD jitter vs fixed baselines).
        #   Both should produce p << 0.05 — jitter vs. no-jitter is clearly distinguishable.
        pairs = []
        if 'prng_fixed' in all_results and 'csprng_fixed' in all_results:
            pairs.append(
                ('prng_fixed', 'csprng_fixed',
                 all_results['prng_fixed']['intervals'],
                 all_results['csprng_fixed']['intervals'],
                 'Entropy quality irrelevant to timing (S3 claim)'))
        if 'prng_fixed' in all_results and 'dead_jitter' in all_results:
            pairs.append(
                ('prng_fixed', 'dead_jitter',
                 all_results['prng_fixed']['intervals'],
                 all_results['dead_jitter']['intervals'],
                 'Mitigation detectable from network (PRNG baseline vs DEAD jitter)'))
        if 'csprng_fixed' in all_results and 'dead_jitter' in all_results:
            pairs.append(
                ('csprng_fixed', 'dead_jitter',
                 all_results['csprng_fixed']['intervals'],
                 all_results['dead_jitter']['intervals'],
                 'Mitigation detectable from network (CSPRNG baseline vs DEAD jitter)'))

        # Fallback: if none of the canonical pairs exist, do all-pairs
        if not pairs:
            cond_keys = list(all_results.keys())
            for i in range(len(cond_keys)):
                for j in range(i + 1, len(cond_keys)):
                    k1, k2 = cond_keys[i], cond_keys[j]
                    pairs.append(
                        (k1, k2,
                         all_results[k1]['intervals'],
                         all_results[k2]['intervals'],
                         f'{k1} vs {k2}'))

        ks_results = []
        print(f"  PAIRWISE KOLMOGOROV-SMIRNOV TESTS")
        print(f"  {'-'*70}")
        for name_a, name_b, intervals_a, intervals_b, pair_label in pairs:
            ks_stat, p_value = ks_2samp(intervals_a, intervals_b)
            conclusion = 'Same distribution' if p_value > 0.05 else 'Different distributions'
            ks_results.append({
                'pair': [name_a, name_b],
                'label': pair_label,
                'ks_stat': round(float(ks_stat), 6),
                'p_value': round(float(p_value), 6),
                'conclusion': conclusion,
            })
            print(f"  KS({name_a} vs {name_b}): D={ks_stat:.4f}, "
                  f"p={p_value:.4f} -> {conclusion}")
            print(f"    {pair_label}")
        print(f"{'='*60}\n")

        # --- Attacker hit probability ---
        from scipy.special import erf
        import math

        print(f"  ATTACKER HIT PROBABILITY (P(hit|W) = erf(W / sqrt(2)*sigma))")
        print(f"  {'-'*60}")
        probe_windows_ms = [1, 10, 100]
        attacker_hit = {}

        for cond_name_ah, cond_data in all_results.items():
            sigma_ms = cond_data['std_ms']
            print(f"\n  {cond_name_ah}  (sigma = {sigma_ms:.1f} ms):")
            hit_dict = {}
            for w in probe_windows_ms:
                p_hit = erf(w / (math.sqrt(2) * sigma_ms)) if sigma_ms > 0 else 1.0
                p_hit = min(p_hit, 1.0)
                hit_dict[f'{w}ms'] = round(p_hit, 6)
                print(f"    +/-{w:>4} ms probe window: P(hit) = {p_hit*100:5.1f}%")
            attacker_hit[cond_name_ah] = hit_dict
        print(f"\n{'='*60}\n")

        # --- Save comparison ---
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        comparison = {
            'experiment': 's3_network_timing',
            'timestamp': timestamp,
            'conditions': {cname: r for cname, r in all_results.items()},
            'ks_tests': ks_results,
            'attacker_hit_probability': attacker_hit,
            'thesis_validation': (
                'Pair 1 p > 0.05: entropy quality does not affect timing distribution. '
                'Pairs 2 & 3 p << 0.05: jitter countermeasure is network-observable.'
            ),
            'environment': 'mininet_kali_ovs',
            'reproducibility': get_reproducibility_metadata(),
        }
        comp_path = os.path.join(RESULTS_DIR, f's3_comparison_{timestamp}.json')
        with open(comp_path, 'w') as f:
            json.dump(comparison, f, indent=2)
        print(f"  Comparison saved to: {comp_path}\n")


if __name__ == '__main__':
    main()
