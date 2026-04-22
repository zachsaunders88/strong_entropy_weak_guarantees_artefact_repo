
import logging
import secrets
import random
import time
from controller.vip_allocator import VIPPool, HostRecord, EntropyProvider, StandardRandomProvider, SecretsProvider

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("reproduce_s2")

def run_replica_round(provider_class, seed=None, num_replicas=5, num_hosts=10):
    replicas = []
    
    # Initialize Replicas
    for i in range(num_replicas):
        if provider_class == StandardRandomProvider:
            # For PRNG, S2 implies "identical conditions", so same seed
            # simulating multiple containers starting from same image/config/time
            s = seed if seed is not None else 12345
            p = StandardRandomProvider()
            random.seed(s) # StandardRandomProvider uses global random.choice usually, 
                           # so we must seed global random for this demo if logic uses it.
                           # Wait, the class implementation uses `random.choice`.
            # To strictly control it per instance might require the WeakBootProvider logic,
            # but let's assume valid 'StandardRandomProvider' usage relies on global state
            # or we enforce the seed before each choice if we want to simulate 'lockstep'.
            # ACTUALLY: If they are separate processes, they have separate memory. 
            # In a single script, they share `random`.
            # To simulate N processes: we should instantiate N providers that don't share state.
            # `StandardRandomProvider` uses `random`, which is shared. 
            # Let's use WeakBootProvider for the PRNG simulation to guarantee isolation/control.
            from controller.vip_allocator import WeakBootProvider
            p = WeakBootProvider(seed=s)
        else:
            p = SecretsProvider()
            
        pool = VIPPool("10.0.1.0/24", entropy_provider=p)
        replicas.append(pool)

    # Run Assignment Round
    # Each replica assigns a vIP for the SAME host
    host_def = HostRecord("h1", "host-1", "10.0.0.1", "s1", 60)
    assignments = []
    
    for i, pool in enumerate(replicas):
        # For WeakBootProvider (PRNG), since they were all Init'd with seed 12345, 
        # they are at state 0.
        vip = pool.assign_initial_vip(host_def)
        assignments.append(vip)
        
    return assignments

def calculate_stats(assignments):
    n = len(assignments)
    # Check for perfect uniformity (All identical) -> Correlation = 1.0 (Bad)
    if all(x == assignments[0] for x in assignments):
        return 1.0, 1.0 # 100% Collision Rate, 100% Correlation
        
    # Check for Pairwise Collisions
    collisions = 0
    pairs = 0
    for i in range(n):
        for j in range(i+1, n):
            pairs += 1
            if assignments[i] == assignments[j]:
                collisions += 1
                
    collision_rate = collisions / pairs if pairs > 0 else 0
    return collision_rate, 0.0 # Return tuple to match unpack signature

def test_s2_correlation():
    logger.info("=== Reproducing S2: Cross-replica Correlation ===")
    
    # CASE 1: PRNG (Standard Random / Weak) under identical conditions
    logger.info("\n--- TEST CASE A: PRNG (Identical Context) ---")
    assignments_prng = run_replica_round(StandardRandomProvider, seed=9999)
    logger.info(f"Replica Assignments: {assignments_prng}")
    
    corr_rate, _ = calculate_stats(assignments_prng)
    if corr_rate == 1.0:
        logger.info(f"RESULT: 100% Cross-Linkage. All replicas made the SAME decision.")
        logger.info("Failure Mode S2 Confirmed for PRNG: Context determines outcome.")
    else:
        logger.info(f"RESULT: {corr_rate:.2%} Collision Rate.")

    # CASE 2: CSPRNG (Secrets)
    logger.info("\n--- TEST CASE B: CSPRNG (Secrets) ---")
    assignments_csprng = run_replica_round(SecretsProvider)
    logger.info(f"Replica Assignments: {assignments_csprng}")
    
    corr_rate_sec, _ = calculate_stats(assignments_csprng)
    logger.info(f"RESULT: {corr_rate_sec:.2%} Collision Rate.")
    
    if corr_rate_sec < 0.2: # For 5 replicas picking from 253 items, collision prob is low (~4%)
        logger.info("SUCCESS: Replicas Diverged. CSPRNG breaks simple state cloning.")
        logger.info("However, 'statistically correlated' inputs remain if they see same candidates.")
        logger.info("Correlation is merely random chance (Birthday Paradox).")
    else:
        logger.warning(f"High collision rate ({corr_rate_sec}) observed for CSPRNG (Luck?)")

if __name__ == "__main__":
    test_s2_correlation()
