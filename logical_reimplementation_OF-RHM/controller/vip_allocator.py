import ipaddress
import random
import time
import secrets
import abc
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

class EntropyProvider(abc.ABC):
    @abc.abstractmethod
    def choice(self, seq, **kwargs):
        pass

    def get_jitter(self, base_seconds: float, jitter_fraction: float = 0.5) -> float:
        """
        Returns a sleep time = base_seconds + jitter.
        Jitter should be random in [0, base_seconds * jitter_fraction].
        Default implementation uses standard random (can be overridden).
        """
        jitter_max = base_seconds * jitter_fraction
        return base_seconds + (random.random() * jitter_max)

class StandardRandomProvider(EntropyProvider):
    def choice(self, seq, **kwargs):
        return random.choice(seq)

class SecretsProvider(EntropyProvider):
    def choice(self, seq, **kwargs):
        return secrets.choice(seq)
    
    def get_jitter(self, base_seconds: float, jitter_fraction: float = 0.5) -> float:
        jitter_max = base_seconds * jitter_fraction
        # Secrets doesn't have a simple 'random float' method, so we improvise
        # Randbelow returns int [0, n). We use microseconds for precision.
        if jitter_max <= 0: return base_seconds
        
        micros = int(jitter_max * 1_000_000)
        rand_micros = secrets.randbelow(micros + 1)
        return base_seconds + (rand_micros / 1_000_000.0)

@dataclass
class HostRecord:
    host_id: str
    name: str
    real_ip: str
    subnet_id: str
    mutation_interval_s: int
    current_vip: Optional[str] = None
    # History of (vip, timestamp_assigned)
    history: List[Tuple[str, float]] = field(default_factory=list)

class WeakBootProvider(EntropyProvider):
    """
    Simulates Failure Mode S1: Boot-time Entropy Starvation.
    Uses a standard PRNG seeded with a low-entropy value (e.g., time).
    """
    def __init__(self, seed=None):
        # specific seed or default to weak source (time)
        if seed is None:
            seed = int(time.time())
        self.rng = random.Random(seed)

    def choice(self, seq, **kwargs):
        return self.rng.choice(seq)
        
    def get_jitter(self, base_seconds: float, jitter_fraction: float = 0.5) -> float:
        jitter_max = base_seconds * jitter_fraction
        return base_seconds + (self.rng.random() * jitter_max)

class DeadEntropyProvider(EntropyProvider):
    """
    Uses the DEAD (Dedicated Entropy Assurance Daemon) sidecar service.
    Fetches cryptographically secure random integers from the daemon via HTTP.
    """
    def __init__(self, server_url: str = "http://127.0.0.1:8000"):
        self.server_url = server_url.rstrip("/")
        import requests
        self.session = requests.Session()

    def choice(self, seq, **kwargs):
        if not seq:
            raise IndexError("Cannot choose from an empty sequence")
        
        # Check for context in kwargs
        context = kwargs.get("context")
        
        # We need an index from 0 to len(seq) - 1
        # We ask DEAD for enough bits to cover this range.
        # But DEAD's API provides 'bits' or 'n bytes'. 
        # Simpler approach: Ask for a 64-bit integer (usually enough for any list)
        # and use modulo.
        # Ideally: Rejection sampling or scaled float, but modulo is fine for POC 
        # if the random space is much larger than len(seq).
        
        try:
            params = {"bits": 64}
            if context:
                params["context"] = context
                
            resp = self.session.get(f"{self.server_url}/entropy_int", params=params, timeout=1.0)
            resp.raise_for_status()
            data = resp.json()
            rand_val = int(data["value"])
            
            # Use modulo to pick index
            idx = rand_val % len(seq)
            return seq[idx]
            
        except Exception as e:
            # In a real DEAD setup, we might want to FAIL CLOSED or retry.
            # For now, raise to demonstrate it's not silent fallback (S5)
            raise RuntimeError(f"DEAD Service Unavailable: {e}") from e

    def get_epoch_key(self, scope: str) -> str:
        """Fetches the current epoch key for the given scope."""
        try:
            resp = self.session.get(f"{self.server_url}/epoch_key", params={"scope": scope}, timeout=1.0)
            resp.raise_for_status()
            return resp.json()["key"]
        except Exception as e:
            raise RuntimeError(f"DEAD epoch key unavailable: {e}") from e

    def get_jitter(self, base_seconds: float, jitter_fraction: float = 0.5) -> float:
        """
        Calculates jitter using entropy from DEAD.
        """
        jitter_max = base_seconds * jitter_fraction
        if jitter_max <= 0: 
            return base_seconds
            
        try:
            # We fetch a random integer (e.g., 32 bits) and normalize it
            resp = self.session.get(f"{self.server_url}/entropy_int", params={"bits": 32}, timeout=0.5)
            resp.raise_for_status()
            rand_val = int(resp.json()["value"])
            max_val = (1 << 32) - 1
            
            # Normalize to [0, 1]
            rand_float = rand_val / max_val
            
            return base_seconds + (rand_float * jitter_max)
            
        except Exception as e:
            # Fallback for reliability (POC)
            print(f"DEAD Jitter Fetch Failed: {e}")
            import random
            return base_seconds + (random.random() * jitter_max)


class VIPPool:
    def __init__(self, pool_cidr: str, reuse_timeout_s: int = 60, entropy_provider: EntropyProvider = None, 
                 max_history_len: int = 1000, replica_id: str = None, coordination_scope: str = None):
        self.network = ipaddress.ip_network(pool_cidr)
        # Exclude network and broadcast addresses
        self.available_vips = [str(ip) for ip in self.network.hosts()]
        self.reuse_timeout_s = reuse_timeout_s
        self.entropy_provider = entropy_provider if entropy_provider else StandardRandomProvider()
        
        self.replica_id = replica_id
        self.coordination_scope = coordination_scope
        self.max_history = max_history_len
        
        # Map of vip -> host_id (currently in use)
        self.in_use_map: Dict[str, str] = {}
        
        # Map of host_id -> List[(vip, timestamp_released)]
        self.history_by_host: Dict[str, List[Tuple[str, float]]] = {}

    def assign_initial_vip(self, host: HostRecord) -> str:
        """Assigns a vIP to a host for the first time.

        If coordination_scope and replica_id are configured (DEAD v1.1), the same
        coordinated permutation logic used by choose_new_vip() is applied here as
        well to avoid cross-replica collisions during initial assignment.
        """
        candidates = [vip for vip in self.available_vips if vip not in self.in_use_map]

        if not candidates:
            raise RuntimeError("No available vIPs in the pool")

        # Coordinated Permutation Logic (Fixes S2)
        if self.coordination_scope and self.replica_id and hasattr(self.entropy_provider, "get_epoch_key"):
            try:
                epoch_key = self.entropy_provider.get_epoch_key(self.coordination_scope)
                r_perm = random.Random(epoch_key)

                candidates = sorted(candidates)
                r_perm.shuffle(candidates)

                try:
                    slot_idx = int(self.replica_id) % len(candidates)
                except (ValueError, TypeError):
                    slot_idx = hash(self.replica_id) % len(candidates)

                vip = candidates[slot_idx]
                self._allocate_vip(host, vip)
                return vip

            except Exception as e:
                print(f"Coordination Failed: {e}")

        vip = self.entropy_provider.choice(candidates, context=host.host_id)
        self._allocate_vip(host, vip)
        return vip

    def choose_new_vip(self, host: HostRecord, now: float = None) -> str:
        """
        Chooses a new vIP for a host, enforcing:
        1. No collision (not currently in use)
        2. No reuse within reuse_timeout_s for this host
        """
        if now is None:
            now = time.time()
            
        # Release current vIP if exists
        if host.current_vip:
            self._release_vip(host, now)

        # Filter candidates
        candidates = []
        for vip in self.available_vips:
            if vip in self.in_use_map:
                continue
            
            # Check reuse history for this host
            if self._is_recently_used(host.host_id, vip, now):
                continue
                
            candidates.append(vip)
            
        if not candidates:
            raise RuntimeError(f"No valid vIPs available for host {host.host_id}")

        # Coordinated Permutation Logic (Fixes S2)
        if self.coordination_scope and self.replica_id and hasattr(self.entropy_provider, "get_epoch_key"):
            try:
                epoch_key = self.entropy_provider.get_epoch_key(self.coordination_scope)
                
                # Seed a PRNG with the epoch Key to get a deterministic shuffle for this window
                # We use the key string to seed a random instance
                # Note: 'random.Random(str)' uses hash of str, which is deterministic in Python 3 (if robust)
                r_perm = random.Random(epoch_key)
                
                # Shuffle the candidate list deterministically for this epoch
                # We sort first to ensure stability before shuffle
                candidates = sorted(candidates)
                r_perm.shuffle(candidates)
                
                # Select index based on Replica ID
                # Effectively partitions the shuffled pool
                try:
                    # If replica_id is numeric (explicit rank), use it directly
                    # This guarantees non-collision if configured correctly (0, 1, 2...)
                    slot_idx = int(self.replica_id) % len(candidates)
                    
                except (ValueError, TypeError):
                    # Fallback to hash, which suffers from Birthday Paradox in small pools
                    slot_idx = hash(self.replica_id) % len(candidates)
                    
                vip = candidates[slot_idx]
                
                self._allocate_vip(host, vip)
                return vip
                
            except Exception as e:
                # Fallback to standard flow
                print(f"Coordination Failed: {e}")
                pass

        vip = self.entropy_provider.choice(candidates, context=host.host_id)
        self._allocate_vip(host, vip)
        return vip

    def _allocate_vip(self, host: HostRecord, vip: str):
        self.in_use_map[vip] = host.host_id
        host.current_vip = vip
        host.history.append((vip, time.time()))

    def _release_vip(self, host: HostRecord, timestamp: float):
        vip = host.current_vip
        if vip:
            del self.in_use_map[vip]
            if host.host_id not in self.history_by_host:
                self.history_by_host[host.host_id] = []
            self.history_by_host[host.host_id].append((vip, timestamp))
            host.current_vip = None

    def _is_recently_used(self, host_id: str, vip: str, now: float) -> bool:
        if host_id not in self.history_by_host:
            return False
            
        for used_vip, released_at in self.history_by_host[host_id]:
            if used_vip == vip:
                if now - released_at < self.reuse_timeout_s:
                    return True
        return False
