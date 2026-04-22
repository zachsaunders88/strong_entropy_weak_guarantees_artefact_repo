#!/usr/bin/env python3
"""
OF-RHM OpenFlow 1.3 Controller — Ryu/os-ken application.

Implements:
  1. ARP proxy for vIPs (replies with gateway MAC)
  2. Three-case packet-in decision logic (vIP hit, admin rIP, blocked rIP)
  3. Mutation-triggered flow deletion via NetworkAwareMutationScheduler
  4. DNS resolution HTTP endpoint on port 8080
"""

import os
import sys
import ipaddress
import struct
import threading
import logging

# --- os-ken / Ryu compatibility layer ---
# os-ken is the maintained fork of Ryu; class names differ (OSKenApp vs RyuApp)
try:
    from os_ken.base import app_manager
    from os_ken.controller import ofp_event
    from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
    from os_ken.ofproto import ofproto_v1_3
    from os_ken.lib.packet import packet, ethernet, arp, ipv4, ether_types
    from os_ken.lib import hub
    AppBase = app_manager.OSKenApp
except ImportError:
    from ryu.base import app_manager
    from ryu.controller import ofp_event
    from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
    from ryu.ofproto import ofproto_v1_3
    from ryu.lib.packet import packet, ethernet, arp, ipv4, ether_types
    from ryu.lib import hub
    AppBase = app_manager.RyuApp

# --- Ensure project root is on sys.path for shared/ imports ---
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from shared.vip_allocator import (
    VIPPool, HostRecord,
    SecretsProvider, WeakBootProvider, DeadEntropyProvider, StandardRandomProvider,
)
from shared.mutation import MutationScheduler
from shared.logger import setup_logger

logger = setup_logger("ofrhm_controller")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_controller_config():
    """Load configuration from environment or defaults."""
    return {
        'hosts_count': int(os.environ.get('OFRHM_HOSTS_COUNT', '3')),
        'hosts_subnet': os.environ.get('OFRHM_HOSTS_SUBNET', '10.0.0'),
        'pool_cidr': os.environ.get('OFRHM_POOL_CIDR', '10.0.1.0/28'),
        'entropy_provider': os.environ.get('OFRHM_ENTROPY_PROVIDER', 'csprng'),
        'mutation_interval_s': float(os.environ.get('OFRHM_MUTATION_INTERVAL', '2.0')),
        'reuse_timeout_s': int(os.environ.get('OFRHM_REUSE_TIMEOUT', '60')),
        'admin_allowlist': os.environ.get('OFRHM_ADMIN_ALLOWLIST', '').split(',') if os.environ.get('OFRHM_ADMIN_ALLOWLIST') else [],
        'use_jitter': os.environ.get('OFRHM_USE_JITTER', '1') not in ('0', 'false', 'False'),
        'dead_url': os.environ.get('OFRHM_DEAD_URL', 'http://127.0.0.1:8000'),
        'dns_port': int(os.environ.get('OFRHM_DNS_PORT', '8080')),
        'gateway_external_ip': os.environ.get('OFRHM_GW_EXT_IP', '192.168.1.1'),
        'gateway_internal_ip': os.environ.get('OFRHM_GW_INT_IP', '10.0.0.254'),
        'client_ip': os.environ.get('OFRHM_CLIENT_IP', '192.168.1.10'),
    }


def make_entropy_provider(name, dead_url='http://127.0.0.1:8000'):
    if name == 'prng':
        return WeakBootProvider()
    elif name == 'csprng':
        return SecretsProvider()
    elif name == 'dead':
        return DeadEntropyProvider(server_url=dead_url)
    else:
        raise ValueError(f"Unknown entropy provider: {name}")


# ---------------------------------------------------------------------------
# NetworkAwareMutationScheduler
# ---------------------------------------------------------------------------

class NetworkAwareMutationScheduler(MutationScheduler):
    """MutationScheduler subclass that notifies the controller on mutation."""

    def __init__(self, vip_pool, hosts, on_mutation_callback, use_jitter=True):
        super().__init__(vip_pool, hosts, use_jitter=use_jitter)
        self.on_mutation = on_mutation_callback

    def mutate_host(self, host):
        old_vip = host.current_vip
        super().mutate_host(host)
        new_vip = host.current_vip
        if old_vip != new_vip and self.on_mutation:
            self.on_mutation(host, old_vip, new_vip)


# ---------------------------------------------------------------------------
# VIP State File Writer (shared with dns_server.py running in gw namespace)
# ---------------------------------------------------------------------------

STATE_FILE = os.environ.get('OFRHM_STATE_FILE', '/tmp/ofrhm_vip_state.json')
_state_write_lock = threading.Lock()


def write_vip_state(controller_ref):
    """Write current VIP state to shared JSON file for the DNS server."""
    import json
    with _state_write_lock:
        hosts = {}
        for name, host in controller_ref.hosts_by_name.items():
            hosts[name] = {
                'host_id': host.host_id,
                'real_ip': host.real_ip,
                'current_vip': host.current_vip,
            }
        state = {
            'hosts': hosts,
            'mutation_interval_s': controller_ref.mutation_interval_s,
            'entropy_provider': controller_ref.cfg['entropy_provider'],
        }
        tmp = STATE_FILE + f'.{os.getpid()}.tmp'
        with open(tmp, 'w') as f:
            json.dump(state, f)
        os.replace(tmp, STATE_FILE)


def start_state_writer(controller_ref, interval=0.25):
    """Periodically write VIP state to disk for the DNS server process."""
    import time

    def _loop():
        while True:
            try:
                write_vip_state(controller_ref)
            except Exception as e:
                logger.error(f"State write error: {e}")
            time.sleep(interval)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    # Also write immediately
    write_vip_state(controller_ref)
    logger.info(f"State writer started -> {STATE_FILE}")
    return thread


# ---------------------------------------------------------------------------
# Main Ryu/os-ken Application
# ---------------------------------------------------------------------------

class OFRHMController(AppBase):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # --- Load configuration ---
        self.cfg = load_controller_config()

        # --- Build entropy provider ---
        self.entropy_provider = make_entropy_provider(
            self.cfg['entropy_provider'],
            self.cfg['dead_url'],
        )

        # --- Build VIP pool ---
        self.vip_pool = VIPPool(
            pool_cidr=self.cfg['pool_cidr'],
            reuse_timeout_s=self.cfg['reuse_timeout_s'],
            entropy_provider=self.entropy_provider,
        )

        # --- Build host records ---
        self.hosts = []
        self.hosts_by_name = {}
        self.hosts_by_rip = {}
        subnet = self.cfg['hosts_subnet']
        self.mutation_interval_s = self.cfg['mutation_interval_s']

        for i in range(1, self.cfg['hosts_count'] + 1):
            rip = f"{subnet}.{i}"
            name = f"host-{i}"
            host = HostRecord(
                host_id=f"h{i}",
                name=name,
                real_ip=rip,
                subnet_id="internal",
                mutation_interval_s=self.mutation_interval_s,
            )
            self.vip_pool.assign_initial_vip(host)
            self.hosts.append(host)
            self.hosts_by_name[name] = host
            self.hosts_by_rip[rip] = host
            logger.info(f"Host {name} ({rip}) -> initial vIP {host.current_vip}")

        # --- Build vIP pool membership set for fast lookup ---
        self.vip_network = ipaddress.ip_network(self.cfg['pool_cidr'])
        self.vip_pool_addrs = set(str(ip) for ip in self.vip_network.hosts())

        # --- Build rIP set for fast lookup ---
        self.rip_set = set(h.real_ip for h in self.hosts)

        # --- Admin allowlist (gateway IPs always allowed) ---
        self.admin_allowlist = set(self.cfg['admin_allowlist'])
        self.admin_allowlist.add(self.cfg['gateway_external_ip'])
        self.admin_allowlist.add(self.cfg['gateway_internal_ip'])

        # --- Network state ---
        self.gw_mac = None          # Learned from gateway's first packet (any interface)
        self.gw_mac_per_dp = {}     # dpid -> gw MAC on that switch
        self.gw_ext_ip = self.cfg['gateway_external_ip']
        self.gw_int_ip = self.cfg['gateway_internal_ip']
        self.client_ip = self.cfg['client_ip']
        self.mac_to_port = {}       # dpid -> {mac -> port}
        self.ip_to_mac = {}         # ip -> mac (learned)
        self.datapaths = {}         # dpid -> datapath

        # --- Start mutation scheduler ---
        self.scheduler = NetworkAwareMutationScheduler(
            self.vip_pool,
            self.hosts,
            on_mutation_callback=self._on_mutation,
            use_jitter=self.cfg['use_jitter'],
        )
        self.scheduler.start()

        # --- Start VIP state writer (shared with dns_server.py in gw namespace) ---
        start_state_writer(self)

        logger.info(f"OF-RHM Controller initialized: "
                     f"hosts={self.cfg['hosts_count']}, "
                     f"pool={self.cfg['pool_cidr']}, "
                     f"entropy={self.cfg['entropy_provider']}, "
                     f"interval={self.mutation_interval_s}s")

    # -------------------------------------------------------------------
    # Switch connection
    # -------------------------------------------------------------------

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """Install table-miss flow entry on each switch."""
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        dpid = datapath.id

        self.datapaths[dpid] = datapath
        self.mac_to_port.setdefault(dpid, {})

        # Table-miss: send to controller
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(
            ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER
        )]
        self._add_flow(datapath, 0, match, actions)

        logger.info(f"Switch {dpid} connected — table-miss installed")

    # -------------------------------------------------------------------
    # Packet-in handler
    # -------------------------------------------------------------------

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']
        dpid = datapath.id

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        if eth is None:
            return

        # Skip LLDP
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        # Learn source MAC -> port
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][eth.src] = in_port

        # --- Handle ARP ---
        arp_pkt = pkt.get_protocol(arp.arp)
        if arp_pkt:
            self._handle_arp(datapath, msg, in_port, eth, arp_pkt)
            return

        # --- Handle IPv4 ---
        ipv4_pkt = pkt.get_protocol(ipv4.ipv4)
        if ipv4_pkt:
            self._handle_ipv4(datapath, msg, in_port, eth, ipv4_pkt)
            return

        # --- Default: flood ---
        self._flood(datapath, msg, in_port)

    # -------------------------------------------------------------------
    # ARP proxy
    # -------------------------------------------------------------------

    def _handle_arp(self, datapath, msg, in_port, eth, arp_pkt):
        """
        ARP proxy for vIPs: reply with gateway MAC for this switch.
        For other ARP, learn and flood.
        """
        dpid = datapath.id

        # Learn IP -> MAC mapping from ARP source
        self.ip_to_mac[arp_pkt.src_ip] = arp_pkt.src_mac

        # Learn gateway MAC per switch from its ARP traffic
        if arp_pkt.src_ip in (self.gw_ext_ip, self.gw_int_ip):
            self.gw_mac = arp_pkt.src_mac
            self.gw_mac_per_dp[dpid] = arp_pkt.src_mac

        # Use per-switch gw MAC, falling back to any known gw MAC
        local_gw_mac = self.gw_mac_per_dp.get(dpid, self.gw_mac)

        if arp_pkt.opcode == arp.ARP_REQUEST:
            dst_ip = arp_pkt.dst_ip

            # If the target is a vIP in our pool, proxy-reply with gw MAC
            if dst_ip in self.vip_pool_addrs and local_gw_mac:
                self._send_arp_reply(
                    datapath, in_port,
                    src_mac=local_gw_mac,
                    src_ip=dst_ip,
                    dst_mac=arp_pkt.src_mac,
                    dst_ip=arp_pkt.src_ip,
                )
                return

            # If target is the gateway IP, and we know gw MAC, reply
            if dst_ip in (self.gw_ext_ip, self.gw_int_ip) and local_gw_mac:
                self._send_arp_reply(
                    datapath, in_port,
                    src_mac=local_gw_mac,
                    src_ip=dst_ip,
                    dst_mac=arp_pkt.src_mac,
                    dst_ip=arp_pkt.src_ip,
                )
                return

        # Default: flood ARP
        self._flood(datapath, msg, in_port)

    def _send_arp_reply(self, datapath, out_port, src_mac, src_ip, dst_mac, dst_ip):
        """Construct and send an ARP reply packet."""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        e = ethernet.ethernet(
            dst=dst_mac,
            src=src_mac,
            ethertype=ether_types.ETH_TYPE_ARP,
        )
        a = arp.arp(
            opcode=arp.ARP_REPLY,
            src_mac=src_mac,
            src_ip=src_ip,
            dst_mac=dst_mac,
            dst_ip=dst_ip,
        )
        p = packet.Packet()
        p.add_protocol(e)
        p.add_protocol(a)
        p.serialize()

        actions = [parser.OFPActionOutput(out_port)]
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=ofproto.OFP_NO_BUFFER,
            in_port=ofproto.OFPP_CONTROLLER,
            actions=actions,
            data=p.data,
        )
        datapath.send_msg(out)

    # -------------------------------------------------------------------
    # IPv4 packet-in: OF-RHM three-case logic
    # -------------------------------------------------------------------

    def _handle_ipv4(self, datapath, msg, in_port, eth, ipv4_pkt):
        src_ip = ipv4_pkt.src
        dst_ip = ipv4_pkt.dst
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        dpid = datapath.id

        # Learn IP -> MAC
        self.ip_to_mac[src_ip] = eth.src

        # ----- Case 1: dst is a currently-assigned vIP -----
        host_id = self.vip_pool.in_use_map.get(dst_ip)
        if host_id:
            host = next((h for h in self.hosts if h.host_id == host_id), None)
            if host is None:
                return

            rip = host.real_ip
            dst_mac = self.ip_to_mac.get(rip)
            if dst_mac is None:
                # We don't know the backend's MAC yet — flood first packet
                # to trigger ARP learning, then install flows on next packet_in
                self._flood(datapath, msg, in_port)
                return

            out_port = self.mac_to_port.get(dpid, {}).get(dst_mac)
            if out_port is None:
                self._flood(datapath, msg, in_port)
                return

            # Determine the port back to the client
            client_mac = self.ip_to_mac.get(src_ip, eth.src)
            client_port = self.mac_to_port.get(dpid, {}).get(client_mac, in_port)

            # Flow timeout: slightly longer than mutation interval to avoid
            # stale flows, but short enough to force re-evaluation
            hard_timeout = max(int(self.mutation_interval_s) + 2, 5)

            # --- Inbound flow: match(dst=vIP) -> rewrite dst to rIP, fwd ---
            match_in = parser.OFPMatch(
                eth_type=0x0800,
                ipv4_dst=dst_ip,
            )
            actions_in = [
                parser.OFPActionSetField(eth_dst=dst_mac),
                parser.OFPActionSetField(ipv4_dst=rip),
                parser.OFPActionOutput(out_port),
            ]
            self._add_flow(datapath, 100, match_in, actions_in,
                           hard_timeout=hard_timeout)

            # --- Outbound flow: match(src=rIP, dst=client_ip) -> rewrite src to vIP, fwd ---
            match_out = parser.OFPMatch(
                eth_type=0x0800,
                ipv4_src=rip,
                ipv4_dst=src_ip,
            )
            actions_out = [
                parser.OFPActionSetField(eth_src=self.gw_mac or eth.dst),
                parser.OFPActionSetField(ipv4_src=dst_ip),
                parser.OFPActionOutput(client_port),
            ]
            self._add_flow(datapath, 100, match_out, actions_out,
                           hard_timeout=hard_timeout)

            # Forward the current packet immediately
            data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
            out = parser.OFPPacketOut(
                datapath=datapath,
                buffer_id=msg.buffer_id,
                in_port=in_port,
                actions=actions_in,
                data=data,
            )
            datapath.send_msg(out)

            logger.info(f"Case 1 — vIP hit: {src_ip} -> {dst_ip} => rewrite to {rip} "
                        f"(host {host.name}, port {out_port})")
            return

        # ----- Case 2 & 3: dst is a real IP (rIP) -----
        if dst_ip in self.rip_set:
            if src_ip in self.admin_allowlist:
                # Case 2: admin access — forward without rewrite
                logger.info(f"Case 2 — admin rIP access: {src_ip} -> {dst_ip}")
                self._flood(datapath, msg, in_port)
                return
            else:
                # Case 3: unauthorized rIP access — DROP
                logger.warning(f"Case 3 — BLOCKED rIP access: {src_ip} -> {dst_ip}")

                # Install a drop flow to suppress future packet_in for this
                match_drop = parser.OFPMatch(
                    eth_type=0x0800,
                    ipv4_src=src_ip,
                    ipv4_dst=dst_ip,
                )
                # Empty actions = drop
                self._add_flow(datapath, 50, match_drop, [],
                               hard_timeout=30, idle_timeout=10)
                return

        # ----- Default: L2 learning switch behavior -----
        dst_mac = eth.dst
        out_port = self.mac_to_port.get(dpid, {}).get(dst_mac)
        if out_port:
            actions = [parser.OFPActionOutput(out_port)]
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst_mac, eth_src=eth.src)
            self._add_flow(datapath, 10, match, actions, idle_timeout=300)

            data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
            out = parser.OFPPacketOut(
                datapath=datapath,
                buffer_id=msg.buffer_id,
                in_port=in_port,
                actions=actions,
                data=data,
            )
            datapath.send_msg(out)
        else:
            self._flood(datapath, msg, in_port)

    # -------------------------------------------------------------------
    # Mutation callback
    # -------------------------------------------------------------------

    def _on_mutation(self, host, old_vip, new_vip):
        """Called by NetworkAwareMutationScheduler when a host's vIP changes."""
        logger.info(f"Mutation: {host.name} {old_vip} -> {new_vip}")

        # Delete flows for the old vIP on all connected switches
        if old_vip:
            for dpid, datapath in list(self.datapaths.items()):
                try:
                    self._delete_flows_for_vip(datapath, old_vip, host.real_ip)
                except Exception as e:
                    logger.error(f"Flow delete error on switch {dpid}: {e}")

        # Update state file for DNS server
        write_vip_state(self)

    def _delete_flows_for_vip(self, datapath, vip, rip):
        """Delete all flows matching a specific vIP (inbound and outbound)."""
        if not vip or not rip:
            return

        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        # Delete inbound flows (match dst=vip)
        match_in = parser.OFPMatch(eth_type=0x0800, ipv4_dst=vip)
        mod_in = parser.OFPFlowMod(
            datapath=datapath,
            command=ofproto.OFPFC_DELETE,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            match=match_in,
        )
        datapath.send_msg(mod_in)

        # Delete outbound flows (match src=rip that was rewriting to old vip)
        match_out = parser.OFPMatch(eth_type=0x0800, ipv4_src=rip)
        mod_out = parser.OFPFlowMod(
            datapath=datapath,
            command=ofproto.OFPFC_DELETE,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
            match=match_out,
        )
        datapath.send_msg(mod_out)

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    def _add_flow(self, datapath, priority, match, actions,
                  hard_timeout=0, idle_timeout=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(
            ofproto.OFPIT_APPLY_ACTIONS, actions
        )]
        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=inst,
            hard_timeout=hard_timeout,
            idle_timeout=idle_timeout,
        )
        datapath.send_msg(mod)

    def _flood(self, datapath, msg, in_port):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        actions = [parser.OFPActionOutput(ofproto.OFPP_FLOOD)]
        data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data,
        )
        datapath.send_msg(out)
