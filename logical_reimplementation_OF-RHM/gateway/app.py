import socket
import threading
import requests
import logging
import time
from typing import Optional, Tuple

from common.config import load_config, AppConfig
from common.logger import setup_logger
from gateway.flow_table import FlowTable, FlowKey, FlowEntry
from gateway.translate import ProxyHandler

logger = setup_logger("gateway_app")

class GatewayService:
    def __init__(self, config: AppConfig):
        self.config = config
        self.flow_table = FlowTable()
        self.running = False
        self.sock = None

    def start(self):
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        port = self.config.gateway.listen_port
        self.sock.bind(('0.0.0.0', port))
        self.sock.listen(100)
        
        logger.info(f"Gateway listening on port {port}")
        
        threading.Thread(target=self._accept_loop, daemon=True).start()
        
        # Start expiry loop
        threading.Thread(target=self._expiry_loop, daemon=True).start()

    def stop(self):
        self.running = False
        if self.sock:
            self.sock.close()

    def _accept_loop(self):
        while self.running:
            try:
                client_sock, addr = self.sock.accept()
                threading.Thread(target=self._handle_connection, args=(client_sock, addr)).start()
            except Exception as e:
                if self.running:
                    logger.error(f"Accept error: {e}")

    def _handle_connection(self, client_sock, addr):
        src_ip, src_port = addr
        
        try:
            # Peek at first bytes to find Host header?
            # Or just read the request?
            # We can't easily peek without consuming in Python socket unless we use MSG_PEEK.
            first_bytes = client_sock.recv(4096, socket.MSG_PEEK)
            dst_ip, _ = self._extract_dst_and_port_from_http(first_bytes)
            
            if not dst_ip:
                # Fallback or error?
                # For testing, maybe we can pass it via a custom header "X-OF-RHM-Dest-IP"?
                logger.warning(f"Could not determine destination for {addr}")
                client_sock.close()
                return

            # Now we have src_ip, src_port, dst_ip, dst_port
            # Parse port from Host header if available, else default to 80
            # We need to modify _extract_dst_from_http to return (ip, port)
            dst_ip, dst_port = self._extract_dst_and_port_from_http(first_bytes)
            
            if not dst_ip:
                # Fallback or error?
                logger.warning(f"Could not determine destination for {addr}")
                client_sock.close()
                return

            key = FlowKey(src_ip, src_port, dst_ip, dst_port, "TCP")
            
            # Flow Table Lookup
            entry = self.flow_table.lookup(key)
            
            if not entry:
                # Miss - Query Controller
                logger.info(f"Flow miss: {key}")
                decision = self._query_controller(src_ip, dst_ip)
                
                if decision and decision.get("allow"):
                    # Install flow
                    rip = decision.get("inbound_dst_rewrite")
                    pinned_vip = decision.get("outbound_src_rewrite")
                    
                    entry = FlowEntry(
                        key=key,
                        dst_ip_to=rip,
                        src_ip_to=pinned_vip
                    )
                    self.flow_table.insert(entry)
                    logger.info(f"Installed flow: {key} -> {rip}")
                else:
                    logger.warning(f"Flow denied by controller: {key}")
                    client_sock.close()
                    return

            # Hit - Proxy
            handler = ProxyHandler(client_sock, addr, entry)
            handler.start()
            
        except Exception as e:
            logger.error(f"Connection handling error: {e}")
            client_sock.close()

    def _extract_dst_and_port_from_http(self, data: bytes) -> Tuple[Optional[str], int]:
        try:
            text = data.decode('utf-8', errors='ignore')
            for line in text.split('\r\n'):
                if line.lower().startswith("host:"):
                    # Host: 10.0.1.5:8080
                    host_part = line.split(":", 1)[1].strip()
                    if ":" in host_part:
                        ip, port = host_part.split(":")
                        return ip, int(port)
                    return host_part, 80
                # Also check X-OF-RHM-Dest-IP for testing
                if line.lower().startswith("x-of-rhm-dest-ip:"):
                    return line.split(":", 1)[1].strip(), 80
        except:
            pass
        return None, 80

    def _query_controller(self, src_ip: str, dst_ip: str) -> Optional[dict]:
        try:
            url = f"{self.config.gateway.controller_url}/packet_in"
            resp = requests.post(url, json={"src_ip": src_ip, "dst_ip": dst_ip}, timeout=5)
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"Controller returned status {resp.status_code}")
            return None
        except Exception as e:
            logger.error(f"Controller query failed: {e}")
            return None

    def _expiry_loop(self):
        while self.running:
            self.flow_table.expire_old()
            time.sleep(5)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True, help='Path to config file')
    args = parser.parse_args()
    
    cfg = load_config(args.config)
    svc = GatewayService(cfg)
    svc.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        svc.stop()
