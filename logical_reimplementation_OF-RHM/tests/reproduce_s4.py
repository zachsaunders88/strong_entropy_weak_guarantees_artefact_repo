
import logging
import pickle
import copy
from controller.vip_allocator import VIPPool, HostRecord, WeakBootProvider, SecretsProvider

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("reproduce_s4")

def run_s4_experiment(provider_class, provider_name):
    logger.info(f"\n--- Testing S4 with {provider_name} ---")
    
    # 1. Initialize "Original" System
    # For PRNG, we use WeakBootProvider which holds a random.Random instance in 'self.rng'.
    # This instance's state WILL be pickled.
    if provider_class == WeakBootProvider:
        provider = WeakBootProvider(seed=12345)
    else:
        provider = SecretsProvider()
        
    original_pool = VIPPool("10.0.1.0/24", entropy_provider=provider)
    
    # 2. Make some decisions to advance state (burn-in)
    host_burn = HostRecord("burn", "burn", "10.0.0.99", "s1", 60)
    original_pool.assign_initial_vip(host_burn)
    logger.info("System initialized and state advanced (1 decision made).")
    
    # 3. SNAPSHOT! (Simulate VM snapshot via Pickle)
    # We serialize the entire VIPPool, which includes the EntropyProvider
    try:
        snapshot_bytes = pickle.dumps(original_pool)
        logger.info(f"Snapshot taken ({len(snapshot_bytes)} bytes).")
    except Exception as e:
        logger.error(f"Snapshot failed: {e}")
        return

    # 4. Restore Clones
    # Restore two separate instances from the same bytes (e.g. 2 VMs booting from same snapshot)
    clone_a = pickle.loads(snapshot_bytes)
    clone_b = pickle.loads(snapshot_bytes)
    logger.info("Restored Clone A and Clone B from snapshot.")
    
    # 5. Execute in Parallel
    host_def = HostRecord("h1", "host-1", "10.0.0.1", "s1", 60)
    
    # We need separate host objects or clear state because they are separate worlds
    host_a = copy.deepcopy(host_def)
    host_b = copy.deepcopy(host_def)
    
    vip_a = clone_a.assign_initial_vip(host_a)
    vip_b = clone_b.assign_initial_vip(host_b)
    
    logger.info(f"Clone A Choice: {vip_a}")
    logger.info(f"Clone B Choice: {vip_b}")
    
    if vip_a == vip_b:
        logger.info(f"RESULT: MATCH. Clones replayed the same decision.")
        if provider_class == WeakBootProvider:
            logger.info("S4 Confirmed for PRNG: Restoring application state resets RNG stream.")
        else:
            logger.warning("Unexpected Match for CSPRNG (1/253 chance?)")
    else:
        logger.info(f"RESULT: DIVERGENCE. Clones made different decisions.")
        if provider_class == SecretsProvider:
            logger.info("S4 Mitigated for CSPRNG: Entropy comes from OS, not pickled state.")
        else:
            logger.error("Unexpected Divergence for PRNG? Pickle should have preserved state.")

if __name__ == "__main__":
    logger.info("=== Reproducing S4: Clone/Snapshot State Reuse ===")
    run_s4_experiment(WeakBootProvider, "PRNG (WeakBootProvider)")
    run_s4_experiment(SecretsProvider, "CSPRNG (SecretsProvider)")
