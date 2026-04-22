#!/usr/bin/env python3
"""
Parametric Mininet topology for the concrete OF-RHM implementation.

Network layout:

    client (192.168.1.10/24)
        |
        s_ext (OVS switch, external segment)
            |
            gw-eth1 (192.168.1.1)
            gw (gateway node — runs Controller + DEAD)
            gw-eth0 (10.0.0.254)
            |
            s1 (OVS switch, internal segment)
                ├── h1 (10.0.0.1)
                ├── h2 (10.0.0.2)
                └── h{n} (10.0.0.{n})

vIP pool: 10.0.1.0/28 (14 usable addresses)
"""

import argparse
import sys
import os

from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController, Host
from mininet.cli import CLI
from mininet.log import setLogLevel, info
from mininet.link import TCLink


class OFRHMTopo(Topo):
    """Parametric OF-RHM topology with external and internal segments."""

    def build(self, n=3, **kwargs):
        # --- External segment ---
        s_ext = self.addSwitch('s2', cls=OVSSwitch, protocols='OpenFlow13')

        # Client on external subnet
        client = self.addHost('client', ip='192.168.1.10/24')
        self.addLink(client, s_ext, cls=TCLink)

        # Gateway node (will have two interfaces configured post-build)
        # Initial IP doesn't matter much — we reconfigure in run()
        gw = self.addHost('gw', ip='10.0.0.254/24')
        self.addLink(gw, s_ext, cls=TCLink)  # gw-eth1 -> s_ext (external)

        # --- Internal segment ---
        s1 = self.addSwitch('s1', cls=OVSSwitch, protocols='OpenFlow13')
        self.addLink(gw, s1, cls=TCLink)  # gw-eth0 -> s1 (internal)

        # Backend hosts on internal subnet
        for i in range(1, n + 1):
            h = self.addHost(
                f'h{i}',
                ip=f'10.0.0.{i}/24',
                defaultRoute='via 10.0.0.254',
            )
            self.addLink(h, s1, cls=TCLink)


def run(n=3, pool_cidr='10.0.1.0/28', mutation_interval=1.0,
        entropy_provider='csprng', experiment_fn=None):
    """
    Build and start the OF-RHM Mininet topology.

    Args:
        n: number of backend hosts
        pool_cidr: vIP pool CIDR
        mutation_interval: seconds between mutations
        entropy_provider: one of 'prng', 'csprng', 'dead'
        experiment_fn: optional callable(net, args) to run instead of CLI
    """
    setLogLevel('info')

    topo = OFRHMTopo(n=n)
    net = Mininet(
        topo=topo,
        controller=lambda name: RemoteController(name, ip='127.0.0.1', port=6633),
        switch=OVSSwitch,
        link=TCLink,
        autoSetMacs=True,
    )
    net.start()

    # --- Configure gateway interfaces ---
    gw = net.get('gw')

    # Mininet creates interfaces in link-addition order:
    #   Link 1 (gw <-> s_ext) -> gw-eth0 (external facing)
    #   Link 2 (gw <-> s1)    -> gw-eth1 (internal facing)
    # But the spec wants gw-eth0 = internal (10.0.0.254), gw-eth1 = external (192.168.1.1)
    # We configure by interface name explicitly.

    # Discover which interface connects to which switch
    gw_intfs = {}
    for intf in gw.intfList():
        if intf.name == 'lo':
            continue
        link = intf.link
        if link:
            # Find the other end of the link
            if link.intf1 == intf:
                peer = link.intf2
            else:
                peer = link.intf1
            peer_node = peer.node
            gw_intfs[peer_node.name] = intf.name

    info(f'*** Gateway interfaces: {gw_intfs}\n')

    ext_intf = gw_intfs.get('s2', 'gw-eth0')
    int_intf = gw_intfs.get('s1', 'gw-eth1')

    gw.cmd(f'ifconfig {ext_intf} 192.168.1.1 netmask 255.255.255.0 up')
    gw.cmd(f'ifconfig {int_intf} 10.0.0.254 netmask 255.255.255.0 up')

    info(f'*** gw {ext_intf} = 192.168.1.1 (external)\n')
    info(f'*** gw {int_intf} = 10.0.0.254 (internal)\n')

    # --- Configure client default route ---
    client = net.get('client')
    client.cmd('ip route del default 2>/dev/null; ip route add default via 192.168.1.1')

    # --- Configure backend hosts default routes ---
    for i in range(1, n + 1):
        h = net.get(f'h{i}')
        h.cmd('ip route del default 2>/dev/null; ip route add default via 10.0.0.254')

    # --- Enable IP forwarding on gateway ---
    gw.cmd('sysctl -w net.ipv4.ip_forward=1')

    # --- Start DNS HTTP server on gateway node ---
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    info('*** Starting DNS HTTP server on gateway (port 8080)...\n')
    gw.cmd(
        f'cd {project_dir} && '
        f'python3 controller/dns_server.py 8080 '
        f'> /tmp/dns_server.log 2>&1 &'
    )
    info('*** DNS server started on gw:8080 (log: /tmp/dns_server.log)\n')

    # --- Start DEAD daemon on gateway if needed ---
    if entropy_provider == 'dead':
        info('*** Starting DEAD entropy daemon on gateway...\n')
        gw.cmd(
            f'cd {project_dir} && '
            f'python3 -m uvicorn dead.server:app '
            f'--host 127.0.0.1 --port 8000 '
            f'> /tmp/dead.log 2>&1 &'
        )
        info('*** DEAD daemon started (log: /tmp/dead.log)\n')

    # --- Store topology metadata for experiments ---
    net.topo_args = {
        'n': n,
        'pool_cidr': pool_cidr,
        'mutation_interval': mutation_interval,
        'entropy_provider': entropy_provider,
    }

    # --- Run experiment or drop into CLI ---
    if experiment_fn:
        try:
            experiment_fn(net, net.topo_args)
        finally:
            net.stop()
    else:
        info('\n*** OF-RHM topology ready. Entering CLI.\n')
        info(f'*** Config: n={n}, pool={pool_cidr}, interval={mutation_interval}s, '
             f'entropy={entropy_provider}\n\n')
        CLI(net)
        net.stop()


def main():
    parser = argparse.ArgumentParser(description='OF-RHM Mininet Topology')
    parser.add_argument('--n', type=int, default=3,
                        help='Number of backend hosts (default: 3)')
    parser.add_argument('--pool-cidr', type=str, default='10.0.1.0/28',
                        help='vIP pool CIDR (default: 10.0.1.0/28)')
    parser.add_argument('--mutation-interval', type=float, default=1.0,
                        help='Mutation interval in seconds (default: 1.0)')
    parser.add_argument('--entropy-provider', type=str, default='csprng',
                        choices=['prng', 'csprng', 'dead'],
                        help='Entropy provider (default: csprng)')
    args = parser.parse_args()

    run(
        n=args.n,
        pool_cidr=args.pool_cidr,
        mutation_interval=args.mutation_interval,
        entropy_provider=args.entropy_provider,
    )


if __name__ == '__main__':
    main()
