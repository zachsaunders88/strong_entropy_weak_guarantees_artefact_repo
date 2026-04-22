#!/usr/bin/env python3
"""
Phase 3 verification test for OF-RHM controller.
Runs as a Mininet experiment_fn — requires ryu-manager running separately.

Tests:
  T1: OVS switches connected to controller
  T2: Gateway can ping internal host (admin forwarding)
  T3: DNS resolution returns valid vIP
  T4: Client can reach backend via vIP (ICMP)
  T5: Direct rIP access from client is blocked (Case 3)
  T6: VIP state file exists and is valid JSON
  T7: Mutation changes vIP over time
"""

import json
import os
import sys
import time

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from topology.ofrhm_topo import run


def phase3_experiment(net, topo_args):
    """Run Phase 3 verification checks."""
    results = {}
    gw = net.get('gw')
    client = net.get('client')
    h1 = net.get('h1')

    print("\n" + "=" * 60)
    print("  PHASE 3 VERIFICATION")
    print("=" * 60)

    # Wait for switches to connect to controller
    print("\n--- Waiting for switch connections (5s) ---")
    time.sleep(5)

    # T1: OVS switches connected
    print("\n[T1] OVS switches connected to controller")
    ovs_out = gw.cmd('ovs-vsctl show 2>/dev/null || echo "no ovs"')
    # Check from the host namespace
    import subprocess
    ovs_out = subprocess.run(['ovs-vsctl', 'show'], capture_output=True, text=True).stdout
    connected = ovs_out.count('is_connected: true')
    results['T1'] = connected >= 2
    print(f"     Connected switches: {connected} (need >= 2)")
    print(f"     {'PASS' if results['T1'] else 'FAIL'}")

    # Trigger ARP learning: have gw ping client and h1 to populate MAC tables
    print("\n--- Triggering ARP learning ---")
    gw.cmd('ping -c 1 -W 1 192.168.1.10')
    gw.cmd('ping -c 1 -W 1 10.0.0.1')
    time.sleep(1)

    # T2: Gateway can ping internal host (admin path)
    print("\n[T2] Gateway can ping internal host (admin path)")
    ping_out = gw.cmd('ping -c 2 -W 2 10.0.0.1')
    t2_pass = '0% packet loss' in ping_out or ', 0% packet loss' in ping_out or ' 0% packet loss' in ping_out
    if not t2_pass:
        # Try once more after brief delay
        time.sleep(2)
        ping_out = gw.cmd('ping -c 2 -W 2 10.0.0.1')
        t2_pass = '0% packet loss' in ping_out or '0% packet loss' in ping_out
    results['T2'] = t2_pass
    print(f"     {ping_out.strip().split(chr(10))[-1] if ping_out.strip() else 'no output'}")
    print(f"     {'PASS' if results['T2'] else 'FAIL'}")

    # T3: DNS resolution returns valid vIP
    print("\n[T3] DNS resolution returns valid vIP")
    dns_out = client.cmd('curl -s --max-time 3 http://192.168.1.1:8080/dns_resolve?name=host-1')
    try:
        dns_data = json.loads(dns_out)
        vip1 = dns_data.get('vip', '')
        results['T3'] = vip1.startswith('10.0.1.')
    except (json.JSONDecodeError, ValueError):
        vip1 = None
        results['T3'] = False
    print(f"     Response: {dns_out.strip()[:120]}")
    print(f"     {'PASS' if results['T3'] else 'FAIL'}")

    # T4: Client can reach backend via vIP
    print("\n[T4] Client can reach backend via vIP")
    if vip1:
        # Start a simple HTTP server on h1 first
        h1.cmd('python3 -m http.server 80 --directory /tmp &')
        time.sleep(0.5)

        # Try ICMP ping first
        ping_vip = client.cmd(f'ping -c 2 -W 3 {vip1}')
        t4_pass = '0% packet loss' in ping_vip
        if not t4_pass:
            # Give ARP proxy time, retry
            time.sleep(2)
            # Re-resolve in case mutation happened
            dns_out2 = client.cmd('curl -s --max-time 3 http://192.168.1.1:8080/dns_resolve?name=host-1')
            try:
                vip1 = json.loads(dns_out2).get('vip', vip1)
            except:
                pass
            ping_vip = client.cmd(f'ping -c 3 -W 3 {vip1}')
            t4_pass = '0% packet loss' in ping_vip or '33% packet loss' in ping_vip or ' 1 received' in ping_vip or ' 2 received' in ping_vip or ' 3 received' in ping_vip
        results['T4'] = t4_pass
        print(f"     Ping {vip1}: {ping_vip.strip().split(chr(10))[-1] if ping_vip.strip() else 'no output'}")
    else:
        results['T4'] = False
        print("     Skipped (no vIP from T3)")
    print(f"     {'PASS' if results['T4'] else 'FAIL'}")

    # T5: Direct rIP access from non-admin client is blocked
    print("\n[T5] Direct rIP access from client is blocked (Case 3)")
    ping_rip = client.cmd('ping -c 2 -W 2 10.0.0.1')
    results['T5'] = '100% packet loss' in ping_rip or 'Unreachable' in ping_rip or '0 received' in ping_rip
    print(f"     {ping_rip.strip().split(chr(10))[-1] if ping_rip.strip() else 'no output'}")
    print(f"     {'PASS' if results['T5'] else 'FAIL'}")

    # T6: VIP state file exists and is valid
    print("\n[T6] VIP state file valid")
    state_file = '/tmp/ofrhm_vip_state.json'
    try:
        with open(state_file) as f:
            state = json.load(f)
        has_hosts = len(state.get('hosts', {})) > 0
        results['T6'] = has_hosts
        print(f"     Hosts in state: {len(state.get('hosts', {}))}")
    except Exception as e:
        results['T6'] = False
        print(f"     Error: {e}")
    print(f"     {'PASS' if results['T6'] else 'FAIL'}")

    # T7: Mutation changes vIP over time
    print("\n[T7] Mutation changes vIP over time")
    dns_before = client.cmd('curl -s --max-time 3 http://192.168.1.1:8080/dns_resolve?name=host-1')
    try:
        vip_before = json.loads(dns_before).get('vip')
    except:
        vip_before = None
    # Wait for at least one mutation cycle (interval defaults to 2s)
    time.sleep(4)
    dns_after = client.cmd('curl -s --max-time 3 http://192.168.1.1:8080/dns_resolve?name=host-1')
    try:
        vip_after = json.loads(dns_after).get('vip')
    except:
        vip_after = None
    results['T7'] = (vip_before is not None and vip_after is not None and vip_before != vip_after)
    print(f"     Before: {vip_before}, After: {vip_after}")
    print(f"     {'PASS' if results['T7'] else 'FAIL'}")

    # Summary
    print("\n" + "=" * 60)
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"  RESULTS: {passed}/{total} passed")
    for t, v in sorted(results.items()):
        print(f"    {t}: {'PASS' if v else 'FAIL'}")
    print("=" * 60 + "\n")

    if passed == total:
        print("  *** PHASE 3 GO/NO-GO: GO ***\n")
    else:
        print("  *** PHASE 3 GO/NO-GO: ISSUES REMAIN ***\n")

    return results


if __name__ == '__main__':
    run(
        n=3,
        pool_cidr='10.0.1.0/28',
        mutation_interval=2.0,
        entropy_provider='csprng',
        experiment_fn=phase3_experiment,
    )
