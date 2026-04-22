
import logging
import random
import collections
from controller.vip_allocator import VIPPool, HostRecord, EntropyProvider, StandardRandomProvider, SecretsProvider

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("reproduce_s5")

# --- MOCKS ---

class FaultyProvider(EntropyProvider):
    """Simulates an entropy source that fails (raises Exception)."""
    def __init__(self, base_provider_name):
        self.name = base_provider_name
        
    def choice(self, seq):
        # Simulate catastrophic failure (e.g. /dev/urandom empty, or remote daemon down)
        raise RuntimeError(f"Entropy Source {self.name} Unreachable")

class ResilientVIPPool(VIPPool):
    """
    Simulates a 'Robust' system that includes a naive fallback to keep running.
    This represents the "Bad Governance" failure mode.
    """
    def assign_initial_vip(self, host: HostRecord) -> str:
        candidates = [vip for vip in self.available_vips if vip not in self.in_use_map]
        if not candidates:
            raise RuntimeError("No available vIPs")
            
        try:
            vip = self.entropy_provider.choice(candidates)
            return vip
        except Exception as e:
            logger.warning(f" [ALERT] Primary Entropy Failed: {e}")
            logger.warning(" [WARN] Falling back to standard random (INSECURE)")
            # FALLBACK to random.choice (The Vulnerability)
            vip = random.choice(candidates)
            self._allocate_vip(host, vip)
            return vip

def run_s5_case(config_name):
    logger.info(f"\n--- Testing S5: {config_name} ---")
    
    # 1. Setup Faulty Provider
    provider = FaultyProvider(config_name)
    
    # 2. Setup Pool with Insecure Fallback Logic
    pool = ResilientVIPPool("10.0.1.0/24", entropy_provider=provider)
    host = HostRecord("h1", "host-1", "10.0.0.1", "s1", 60)
    
    # 3. Trigger Allocation
    try:
        vip = pool.assign_initial_vip(host)
        logger.info(f"Assigned vIP: {vip}")
        
        # 4. Analyze Consequence
        if config_name == "CSPRNG":
            logger.info("RESULT: DOWNGRADE DETECTED.")
            logger.info("System silently switched from High Entropy (Configured) to Low Entropy (Fallback).")
            logger.info("Attacker can now use S1 (Boot-starvation) or S2 (State Cloning) against this fallback.")
        else:
            logger.info("RESULT: FALLBACK OK (Low -> Low).")
            logger.info("System failed over, but security posture didn't change meaningfully.")
            
    except Exception as e:
        logger.error(f"System Crashed: {e}")
        logger.info("RESULT: Availability Failure (Fail Closed). Better for security, bad for uptime.")

if __name__ == "__main__":
    logger.info("=== Reproducing S5: Entropy Degradation ===")
    
    # Case 1: Configured for PRNG, fails back to PRNG
    run_s5_case("PRNG")
    
    # Case 2: Configured for CSPRNG, fails back to PRNG
    run_s5_case("CSPRNG")
