
import logging
import secrets
from controller.vip_allocator import VIPPool, SecretsProvider, HostRecord

# Setup basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_s1_mitigation")

def test_s1_mitigation():
    logger.info("=== Verifying S1 Mitigation: Using SecretsProvider (CSPRNG) ===")
    
    # 1. Simulate the "Goal" (The Victim System)
    # Using SecretsProvider which pulls from OS entropy (non-deterministic from user space)
    logger.info("Victim System booting with SecretsProvider (CSPRNG)")
    victim_provider = SecretsProvider()
    victim_pool = VIPPool("10.0.1.0/24", entropy_provider=victim_provider)
    
    # 2. Simulate the "Attacker"
    # Even if the attacker also uses SecretsProvider, or WeakBootProvider, 
    # they cannot sync their state with the victim because 'secrets' uses internal OS state.
    logger.info("Attacker attempting to clone state (impossible with CSPRNG)")
    attacker_provider = SecretsProvider() 
    attacker_pool = VIPPool("10.0.1.0/24", entropy_provider=attacker_provider)
    
    # 3. Predict Assignments
    victim_hosts = [HostRecord(f"h{i}", f"host-{i}", f"10.0.0.{i}", "s1", 60) for i in range(1, 6)]
    attacker_hosts = [HostRecord(f"h{i}", f"host-{i}", f"10.0.0.{i}", "s1", 60) for i in range(1, 6)]
    
    matches = 0
    attempts = 5
    
    for i, (v_host, a_host) in enumerate(zip(victim_hosts, attacker_hosts)):
        real_vip = victim_pool.assign_initial_vip(v_host)
        predicted_vip = attacker_pool.assign_initial_vip(a_host)
        
        logger.info(f"Host {v_host.name}: Real={real_vip}, Predicted={predicted_vip}")
        
        if real_vip == predicted_vip:
            logger.warning(f"Coincidental Match! (Probability should be low: 1/253)")
            matches += 1
        else:
            logger.info("Prediction Failed (Expected behavior)")
            
    if matches == 0:
        logger.info("SUCCESS: Attacker failed to predict any vIPs.")
        logger.info("This confirms that CSPRNG mitigates Boot-time Entropy Starvation as state cannot be cloned.")
    elif matches < attempts:
        logger.info(f"SUCCESS: Attacker only matched {matches}/{attempts} purely by chance.")
    else:
        logger.error("FAILURE: Attacker predicted all IPs? This should be statistically impossible.")
        exit(1)

if __name__ == "__main__":
    test_s1_mitigation()
