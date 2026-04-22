
import time
import logging
from controller.vip_allocator import VIPPool, WeakBootProvider, HostRecord

# Setup basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reproduce_s1")

def test_s1_predictability():
    logger.info("=== Reproducing S1: Boot-time Entropy Starvation ===")
    
    # 1. Simulate the "Goal" (The Victim System)
    # The system boots at a specific time (simulated by a specific seed)
    boot_seed = 123456789 # Fixed for reproducibility, or int(time.time())
    logger.info(f"Victim System booting with seed: {boot_seed}")
    
    victim_provider = WeakBootProvider(seed=boot_seed)
    victim_pool = VIPPool("10.0.1.0/24", entropy_provider=victim_provider)
    
    # 2. Simulate the "Attacker"
    # The attacker guesses the seed (e.g., knows the boot time roughly)
    logger.info(f"Attacker guessing seed: {boot_seed}")
    attacker_provider = WeakBootProvider(seed=boot_seed)
    attacker_pool = VIPPool("10.0.1.0/24", entropy_provider=attacker_provider)
    
    # 3. Predict Assignments
    # We create identical host records for both context
    victim_hosts = [HostRecord(f"h{i}", f"host-{i}", f"10.0.0.{i}", "s1", 60) for i in range(1, 6)]
    attacker_hosts = [HostRecord(f"h{i}", f"host-{i}", f"10.0.0.{i}", "s1", 60) for i in range(1, 6)]
    
    for i, (v_host, a_host) in enumerate(zip(victim_hosts, attacker_hosts)):
        # Victim assigns a vIP
        # Note: assign_initial_vip calls provider.choice(candidates)
        # We must ensure the candidate list order is deterministic for the RNG to pick the same index/element.
        # VIPPool generates available_vips from network.hosts(), which yields in order.
        # So as long as in_use_map is empty or identical, candidates are identical.
        
        real_vip = victim_pool.assign_initial_vip(v_host)
        predicted_vip = attacker_pool.assign_initial_vip(a_host)
        
        logger.info(f"Host {v_host.name}: Real={real_vip}, Predicted={predicted_vip}")
        
        if real_vip != predicted_vip:
            logger.error("PREDICTION FAILED! Entropy was sufficient/divergent.")
            exit(1)
            
    logger.info("SUCCESS: Attacker successfully predicted all vIP assignments!")
    logger.info("This confirms Failure Mode S1: If seed is known/weak, assignments are predictable.")

if __name__ == "__main__":
    test_s1_predictability()
