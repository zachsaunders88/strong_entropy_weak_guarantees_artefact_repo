#!/usr/bin/env python3
"""
Mininet topology for OF-RHM.
Requires Mininet to be installed.
"""

import os
import sys
from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import Node, OVSKernelSwitch, Controller, RemoteController
from mininet.cli import CLI
from mininet.log import setLogLevel, info
from mininet.link import TCLink

class OFRHMTopo(Topo):
    def build(self, n=3):
        # Add Gateway (acting as a router/switch)
        # We use a Host for the Gateway to run our Python GatewayService
        gateway = self.addHost('gw', ip='10.0.1.254/24')

        # Add Client (External)
        client = self.addHost('client', ip='192.168.1.10/24')
        
        # Add Internal Hosts
        # They are on a private subnet 10.0.0.0/24?
        # In our config we used 10.0.0.0/24 for real IPs.
        # Gateway needs interfaces on both?
        # For simplicity in this logical topo, let's connect everyone to a central switch
        # and let the Gateway node act as the logical controller/gateway.
        # BUT, the requirement says "gateway node in between".
        
        # Topology: Client <-> Switch1 <-> Gateway <-> Switch2 <-> Servers
        # Or simpler: Client <-> Gateway <-> Servers (direct links)
        
        # Let's use a central switch to connect everyone, but enforce logical separation?
        # Or better:
        # Client --(link)-- Gateway --(link)-- Switch --(link)-- Servers
        
        # Create Switch for internal network
        s1 = self.addSwitch('s1')
        
        # Link Gateway to Switch (Internal Interface)
        self.addLink(gateway, s1)
        
        # Link Gateway to Client (External Interface)
        self.addLink(client, gateway)
        
        # Add Servers
        for i in range(1, n + 1):
            h = self.addHost(f'h{i}', ip=f'10.0.0.{i}/24', defaultRoute='via 10.0.0.254')
            self.addLink(h, s1)

def run():
    topo = OFRHMTopo(n=3)
    net = Mininet(topo=topo, link=TCLink)
    net.start()
    
    # Configure Gateway Interfaces
    gw = net.get('gw')
    # gw-eth0 is connected to s1 (internal) -> 10.0.0.254
    # gw-eth1 is connected to client (external) -> 192.168.1.1
    
    gw.cmd('ifconfig gw-eth0 10.0.0.254 netmask 255.255.255.0 up')
    gw.cmd('ifconfig gw-eth1 192.168.1.1 netmask 255.255.255.0 up')
    
    # Configure Client
    client = net.get('client')
    client.cmd('route add default gw 192.168.1.1')
    
    # Enable forwarding on Gateway?
    # gw.cmd('sysctl -w net.ipv4.ip_forward=1')
    # But we are running a USERSPACE proxy (GatewayService).
    # So we don't need kernel forwarding. We just need the IPs to be reachable.
    
    # Start Controller Service on Gateway node (or separate node?)
    # Requirement: "Controller process... Gateway daemon..."
    # We can run them on 'gw' node or a separate 'c0' node.
    # Let's run them on 'gw' for simplicity or 'c0' connected to 'gw'.
    # Let's run on 'gw' in background.
    
    info('*** Starting Controller...\n')
    gw.cmd('python3 -m controller --config configs/mininet.yaml > controller.log 2>&1 &')
    
    info('*** Starting Gateway...\n')
    gw.cmd('python3 -m gateway --config configs/mininet.yaml > gateway.log 2>&1 &')
    
    # Start Servers
    for i in range(1, 4):
        h = net.get(f'h{i}')
        h.cmd(f'python3 tests/server.py --port 80 > h{i}.log 2>&1 &')

    CLI(net)
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    run()
