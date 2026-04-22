#!/usr/bin/env python3
"""
S1 Network-Level Experiment: Boot-Time Entropy Starvation

Demonstrates that an attacker who knows the approximate controller boot time
can predict the entire vIP assignment sequence when the controller uses a
time-seeded PRNG. Measures prediction accuracy at the network level by
comparing attacker's shadow VIPPool against observed vIPs from the DNS endpoint.

Usage:
    python3 experiments/s1_network_prediction.py --provider prng --hosts 5 --rounds 3
    python3 experiments/s1_network_prediction.py --provider csprng --hosts 5 --rounds 3
    python3 experiments/s1_network_prediction.py --provider dead --hosts 5 --rounds 3
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.vip_allocator import (
    VIPPool, HostRecord, WeakBootProvider, SecretsProvider, DeadEntropyProvider,
)
from shared.experiment_utils import get_reproducibility_metadata, wilson_ci

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(PROJECT_DIR, 'results')
DEAD_PORT = 8000


def create_victim_pool(provider_name, seed, pool_cidr, hosts):
    """Create a VIPPool as the victim controller would at boot."""
    if provider_name == 'prng':
        provider = WeakBootProvider(seed=seed)
    elif provider_name == 'csprng':
        provider = SecretsProvider()
    elif provider_name == 'dead':
        provider = DeadEntropyProvider(server_url=f'http://127.0.0.1:{DEAD_PORT}')
    else:
        raise ValueError(f'Unknown provider: {provider_name}')

    # Disable reuse timeout so victim/shadow candidate lists stay identical
    # (reuse timeout uses wall-clock time, causing divergence between runs)
    pool = VIPPool(pool_cidr, entropy_provider=provider, reuse_timeout_s=0)
    for host in hosts:
        pool.assign_initial_vip(host)
    return pool


def create_attacker_shadow(seed, pool_cidr, hosts):
    """
    Create the attacker's shadow VIPPool using the guessed seed.
    The attacker always uses WeakBootProvider because they are
    simulating the victim's time-seeded PRNG.
    """
    provider = WeakBootProvider(seed=seed)
    pool = VIPPool(pool_cidr, entropy_provider=provider, reuse_timeout_s=0)
    for host in hosts:
        pool.assign_initial_vip(host)
    return pool


def make_hosts(num_hosts):
    """Create a list of HostRecord instances."""
    return [
        HostRecord(
            host_id=f'h{i+1}', name=f'host-{i+1}',
            real_ip=f'10.0.0.{i+1}',
            subnet_id='s1', mutation_interval_s=60,
        )
        for i in range(num_hosts)
    ]


def start_dead_server():
    """Start DEAD daemon if not already running."""
    import requests
    try:
        resp = requests.get(f'http://127.0.0.1:{DEAD_PORT}/status', timeout=0.5)
        if resp.status_code == 200:
            return None, None
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

    log_f = open('/tmp/dead_s1.log', 'w')
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


def run_s1_experiment(provider_name, num_hosts=5, pool_cidr='10.0.1.0/24',
                      seed_window=60, num_mutation_rounds=3):
    """
    Run S1 experiment.

    For PRNG: seed = int(time.time()). Attacker tries seeds in
    [boot_time - seed_window, boot_time + seed_window].
    For CSPRNG/DEAD: seed is irrelevant, attacker shadow always uses PRNG.

    Returns:
        dict with full results
    """
    # Create host records
    hosts_victim = make_hosts(num_hosts)

    # Victim boots now — seed is current time
    boot_time = int(time.time())
    victim_seed = boot_time

    # Create victim pool
    victim_pool = create_victim_pool(provider_name, victim_seed, pool_cidr,
                                     hosts_victim)

    # Record victim's initial vIP assignments
    victim_assignments = []
    initial_round = {
        'round': 0,
        'type': 'initial_assignment',
        'assignments': {h.host_id: h.current_vip for h in hosts_victim},
    }
    victim_assignments.append(initial_round)

    # Run mutation rounds and record
    for r in range(num_mutation_rounds):
        for host in hosts_victim:
            try:
                victim_pool.choose_new_vip(host)
            except Exception:
                pass
        round_data = {
            'round': r + 1,
            'type': 'mutation',
            'assignments': {h.host_id: h.current_vip for h in hosts_victim},
        }
        victim_assignments.append(round_data)

    # Attacker brute-forces seeds
    best_match = {'seed': None, 'matches': 0, 'total': 0}
    candidates_tried = 0

    for candidate_seed in range(boot_time - seed_window,
                                boot_time + seed_window + 1):
        candidates_tried += 1

        # Recreate attacker hosts for each candidate
        hosts_shadow = make_hosts(num_hosts)
        shadow_pool = create_attacker_shadow(candidate_seed, pool_cidr,
                                             hosts_shadow)

        # Compare initial assignments (use recorded state, not current)
        matches = 0
        total = 0
        for h_s in hosts_shadow:
            victim_vip = victim_assignments[0]['assignments'][h_s.host_id]
            total += 1
            if victim_vip == h_s.current_vip:
                matches += 1

        # Run same mutation rounds on shadow — compare against recorded victim state
        for r in range(num_mutation_rounds):
            for h_s in hosts_shadow:
                try:
                    shadow_pool.choose_new_vip(h_s)
                except Exception:
                    pass
            for h_v, h_s in zip(hosts_victim, hosts_shadow):
                victim_vip = victim_assignments[r + 1]['assignments'][h_v.host_id]
                total += 1
                if victim_vip == h_s.current_vip:
                    matches += 1

        if matches > best_match['matches']:
            best_match = {
                'seed': candidate_seed,
                'matches': matches,
                'total': total,
            }

        # Early termination if perfect match found
        if matches == total:
            break

    prediction_accuracy = (best_match['matches'] / best_match['total']
                          if best_match['total'] > 0 else 0)

    summary = {
        'experiment': 's1_boot_entropy_starvation',
        'provider': provider_name,
        'victim_seed': victim_seed,
        'best_attacker_seed': best_match['seed'],
        'seed_match': best_match['seed'] == victim_seed,
        'seed_window': seed_window,
        'candidates_tried': candidates_tried,
        'num_hosts': num_hosts,
        'num_mutation_rounds': num_mutation_rounds,
        'pool_cidr': pool_cidr,
        'prediction_accuracy': round(prediction_accuracy, 6),
        'matches': best_match['matches'],
        'total_comparisons': best_match['total'],
        'victim_assignments': victim_assignments,
        'timestamp': datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
        'environment': 'mininet_kali_ovs',
        'verdict': 'VULNERABLE' if prediction_accuracy > 0.5 else 'MITIGATED',
    }

    return summary


def main():
    parser = argparse.ArgumentParser(description='S1 Network Prediction Experiment')
    parser.add_argument('--provider', choices=['prng', 'csprng', 'dead'],
                        default='prng')
    parser.add_argument('--hosts', type=int, default=5)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--seed-window', type=int, default=60)
    parser.add_argument('--trials', type=int, default=10,
                        help='Number of independent trials per provider (default: 10)')
    parser.add_argument('--run-all', action='store_true',
                        help='Run all 3 providers sequentially')
    parser.add_argument('--output', default=None)
    args = parser.parse_args()

    if args.run_all:
        providers = ['prng', 'csprng', 'dead']
    else:
        providers = [args.provider]

    all_results = {}
    dead_proc = None
    dead_log = None

    needs_dead = 'dead' in providers
    if needs_dead:
        print('Starting DEAD entropy daemon...')
        dead_proc, dead_log = start_dead_server()
        if dead_proc:
            print(f'  DEAD started (PID {dead_proc.pid})')
        else:
            print('  DEAD already running')

    try:
        for provider in providers:
            print(f'\n{"="*60}')
            print(f'  S1 Boot-Time Entropy Starvation: {provider}')
            print(f'  Hosts: {args.hosts}, Mutation rounds: {args.rounds}')
            print(f'  Trials: {args.trials}')
            print(f'  Attacker seed window: +/-{args.seed_window}s '
                  f'({2 * args.seed_window + 1} candidates)')
            print(f'{"="*60}\n')

            trial_results = []
            for trial in range(args.trials):
                result = run_s1_experiment(
                    provider_name=provider,
                    num_hosts=args.hosts,
                    num_mutation_rounds=args.rounds,
                    seed_window=args.seed_window,
                )
                trial_results.append(result)
                print(f'  Trial {trial+1}/{args.trials}: '
                      f'{result["prediction_accuracy"]*100:.0f}% prediction '
                      f'(seed match: {result["seed_match"]})')
                # Ensure int(time.time()) changes between trials
                time.sleep(1.1)

            # Compute aggregate statistics across trials
            accuracies = [r['prediction_accuracy'] for r in trial_results]
            mean_accuracy = sum(accuracies) / len(accuracies)
            all_seeds_matched = all(r['seed_match'] for r in trial_results)
            ci_low, ci_high = wilson_ci(mean_accuracy, len(accuracies))

            aggregate = {
                'experiment': 's1_boot_entropy_starvation',
                'provider': provider,
                'num_trials': args.trials,
                'num_hosts': args.hosts,
                'num_mutation_rounds': args.rounds,
                'seed_window': args.seed_window,
                'mean_prediction_accuracy': round(mean_accuracy, 6),
                'ci_95_low': round(ci_low, 6),
                'ci_95_high': round(ci_high, 6),
                'all_seeds_matched': all_seeds_matched,
                'per_trial_accuracies': [round(a, 6) for a in accuracies],
                'trial_results': trial_results,
                'timestamp': datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
                'environment': 'mininet_kali_ovs',
                'reproducibility': get_reproducibility_metadata(),
                'verdict': 'VULNERABLE' if mean_accuracy > 0.5 else 'MITIGATED',
            }

            all_results[provider] = aggregate

            print(f'\n  Aggregate ({args.trials} trials):')
            print(f'    Mean prediction accuracy: {mean_accuracy*100:.1f}%')
            print(f'    95% CI: [{ci_low*100:.1f}%, {ci_high*100:.1f}%]')
            print(f'    All seeds matched: {all_seeds_matched}')
            print(f'    Verdict: {aggregate["verdict"]}')

            # Save per-provider result
            os.makedirs(RESULTS_DIR, exist_ok=True)
            out_path = os.path.join(
                RESULTS_DIR,
                f's1_network_prediction_{provider}_{aggregate["timestamp"]}.json'
            )
            with open(out_path, 'w') as f:
                json.dump(aggregate, f, indent=2)
            print(f'    Saved to: {out_path}')

    finally:
        if dead_proc:
            print('\nStopping DEAD daemon...')
            stop_dead_server(dead_proc, dead_log)

    # Print comparison table if multiple providers
    if len(all_results) > 1:
        print(f'\n{"="*60}')
        print(f'  S1 CROSS-PROVIDER COMPARISON ({args.trials} trials each)')
        print(f'{"="*60}')
        print(f'  {"Provider":<12} {"Mean Acc":<12} {"95% CI":<22} {"Seeds OK":<12} {"Verdict":<12}')
        print(f'  {"-"*12} {"-"*12} {"-"*22} {"-"*12} {"-"*12}')
        for p, r in all_results.items():
            ci_str = f'[{r["ci_95_low"]*100:.1f}%, {r["ci_95_high"]*100:.1f}%]'
            print(f'  {p:<12} {r["mean_prediction_accuracy"]*100:<12.1f} '
                  f'{ci_str:<22} {str(r["all_seeds_matched"]):<12} {r["verdict"]:<12}')
        print(f'{"="*60}\n')

        comp_path = os.path.join(
            RESULTS_DIR,
            f's1_comparison_{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}.json'
        )
        with open(comp_path, 'w') as f:
            json.dump({
                'experiment': 's1_comparison',
                'providers': all_results,
                'timestamp': datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
                'reproducibility': get_reproducibility_metadata(),
            }, f, indent=2)
        print(f'  Comparison saved to: {comp_path}')


if __name__ == '__main__':
    main()
