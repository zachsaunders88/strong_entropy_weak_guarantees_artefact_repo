import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
import threading

@dataclass(frozen=True)
class FlowKey:
    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int
    proto: str

@dataclass
class FlowEntry:
    key: FlowKey
    # Translations
    dst_ip_to: Optional[str] = None # For inbound: replace dst vIP with rIP
    src_ip_to: Optional[str] = None # For outbound: replace src rIP with pinned vIP
    
    created_at: float = 0.0
    last_seen: float = 0.0
    idle_timeout: int = 60

class FlowTable:
    def __init__(self):
        self._table: Dict[FlowKey, FlowEntry] = {}
        self._lock = threading.RLock()

    def lookup(self, key: FlowKey) -> Optional[FlowEntry]:
        with self._lock:
            entry = self._table.get(key)
            if entry:
                # Update last_seen on hit? 
                # Usually data plane updates this, but for logical sim we can do it here or let caller do it.
                # Let's let caller do it if they want to simulate packet arrival.
                # But for simplicity, let's update it here as "packet matched".
                # Actually, dataclass is frozen? No, FlowKey is frozen. FlowEntry is not.
                entry.last_seen = time.time()
            return entry

    def insert(self, entry: FlowEntry):
        with self._lock:
            entry.created_at = time.time()
            entry.last_seen = entry.created_at
            self._table[entry.key] = entry

    def expire_old(self, now: float = None) -> int:
        if now is None:
            now = time.time()
        
        expired_keys = []
        with self._lock:
            for key, entry in self._table.items():
                if now - entry.last_seen > entry.idle_timeout:
                    expired_keys.append(key)
            
            for key in expired_keys:
                del self._table[key]
                
        return len(expired_keys)

    def get_all(self) -> Dict[FlowKey, FlowEntry]:
        with self._lock:
            return self._table.copy()
