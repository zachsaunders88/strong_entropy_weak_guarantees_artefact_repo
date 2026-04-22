import logging
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from controller.vip_allocator import HostRecord, VIPPool
from common.logger import setup_logger

logger = setup_logger("controller_logic")

@dataclass
class FlowActions:
    # Inbound (Client -> Server)
    inbound_dst_rewrite: Optional[str] = None
    # Outbound (Server -> Client)
    outbound_src_rewrite: Optional[str] = None
    allow: bool = False

class ControllerLogic:
    def __init__(self, vip_pool: VIPPool, hosts: List[HostRecord]):
        self.vip_pool = vip_pool
        self.hosts_by_id = {h.host_id: h for h in hosts}
        self.hosts_by_name = {h.name: h for h in hosts}
        self.hosts_by_rip = {h.real_ip: h for h in hosts}
        
        # Admin allowlist for rIP access (source IPs)
        self.admin_allowlist: List[str] = []

    def get_host_by_vip(self, vip: str) -> Optional[HostRecord]:
        # This is O(N) or needs a reverse map. 
        # Since VIPPool has in_use_map (vip -> host_id), we can use that.
        host_id = self.vip_pool.in_use_map.get(vip)
        if host_id:
            return self.hosts_by_id.get(host_id)
        return None

    def resolve_dns(self, hostname: str) -> Optional[str]:
        host = self.hosts_by_name.get(hostname)
        if not host:
            logger.warning(f"DNS resolve failed for {hostname}")
            return None
        logger.info(f"DNS resolve for {hostname} -> {host.current_vip}")
        return host.current_vip

    def handle_packet_in(self, src_ip: str, dst_ip: str) -> FlowActions:
        """
        Decides what to do with a new flow.
        Implements Algorithm 1 semantics.
        """
        logger.info(f"Packet-in: src={src_ip} dst={dst_ip}")
        
        # Case 1: Destination is a vIP
        target_host = self.get_host_by_vip(dst_ip)
        if target_host:
            # Authorized access via vIP
            # Pin the vIP for this flow (effectively done by installing the rule with this specific vIP)
            pinned_vip = dst_ip
            real_ip = target_host.real_ip
            
            logger.info(f"Flow allowed: {src_ip} -> {dst_ip} (vIP) mapped to {real_ip}")
            
            return FlowActions(
                allow=True,
                inbound_dst_rewrite=real_ip,
                outbound_src_rewrite=pinned_vip
            )

        # Case 2: Destination is a rIP
        target_host = self.hosts_by_rip.get(dst_ip)
        if target_host:
            # Direct access to rIP
            if src_ip in self.admin_allowlist:
                logger.info(f"Admin access allowed: {src_ip} -> {dst_ip} (rIP)")
                return FlowActions(allow=True)
            else:
                logger.warning(f"Direct access denied: {src_ip} -> {dst_ip} (rIP)")
                return FlowActions(allow=False)

        # Case 3: Unknown destination (could be external internet, or non-managed host)
        # For this logical sim, we might default to allow or deny.
        # Let's assume deny for now if it's not a known host.
        # Or maybe allow if it's external?
        # The requirements focus on protecting the servers.
        # If dst is not in our system, we probably just let it through (gateway default) or deny.
        # Let's log and deny to be safe/strict for now.
        logger.warning(f"Unknown destination denied: {src_ip} -> {dst_ip}")
        return FlowActions(allow=False)

    def add_admin_ip(self, ip: str):
        self.admin_allowlist.append(ip)
