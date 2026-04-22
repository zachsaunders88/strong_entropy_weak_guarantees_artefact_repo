import socket
import threading
import logging
from typing import Optional

from common.logger import setup_logger
from gateway.flow_table import FlowEntry

logger = setup_logger("gateway_translate")

class ProxyHandler:
    def __init__(self, client_sock: socket.socket, client_addr, flow_entry: FlowEntry):
        self.client_sock = client_sock
        self.client_addr = client_addr
        self.flow_entry = flow_entry
        self.server_sock: Optional[socket.socket] = None
        self.running = False

    def start(self):
        self.running = True
        target_ip = self.flow_entry.dst_ip_to
        target_port = self.flow_entry.key.dst_port
        
        try:
            # Connect to the real server (rIP)
            self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_sock.connect((target_ip, target_port))
            
            logger.info(f"Proxy established: {self.client_addr} -> {target_ip}:{target_port} (vIP was {self.flow_entry.key.dst_ip})")
            
            # Start bidirectional forwarding
            t1 = threading.Thread(target=self._forward, args=(self.client_sock, self.server_sock, "client->server"))
            t2 = threading.Thread(target=self._forward, args=(self.server_sock, self.client_sock, "server->client"))
            t1.start()
            t2.start()
            
            t1.join()
            t2.join()
            
        except Exception as e:
            logger.error(f"Proxy error: {e}")
        finally:
            self.close()

    def _forward(self, src: socket.socket, dst: socket.socket, direction: str):
        try:
            while self.running:
                data = src.recv(4096)
                if not data:
                    break
                dst.sendall(data)
        except Exception:
            pass # Connection closed or error
        finally:
            self.running = False
            # Close both sides if one fails
            try:
                src.shutdown(socket.SHUT_RDWR)
                src.close()
            except: pass
            try:
                dst.shutdown(socket.SHUT_RDWR)
                dst.close()
            except: pass

    def close(self):
        self.running = False
        if self.client_sock:
            try:
                self.client_sock.close()
            except: pass
        if self.server_sock:
            try:
                self.server_sock.close()
            except: pass
