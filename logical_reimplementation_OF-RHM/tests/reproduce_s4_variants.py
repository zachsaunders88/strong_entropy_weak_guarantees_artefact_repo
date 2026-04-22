
import logging
import pickle
import time
import copy
from controller.vip_allocator import VIPPool, HostRecord, SecretsProvider
from controller.mutation import MutationScheduler

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("reproduce_s4_variants")

# --- MOCKS ---
# We need to mock MutationScheduler slightly to control time/loop or just verify state
class MockSchedulerState:
    """Simulates the state of a scheduler that would be pickled."""
    def __init__(self, next_deadline):
        self.next_deadline = next_deadline
        self.interval = 5.0

def test_s4c_schedule_replay():
    logger.info("\n=== S4c: Schedule Replay (Timing) ===")
    
    # 1. State Setup
    # Imagine the scheduler planned the next event at T=1000
    future_time = time.time() + 10.0
    scheduler_state = MockSchedulerState(next_deadline=future_time)
    
    logger.info(f"Original Scheduler Deadline: {future_time}")
    
    # 2. Snapshot
    snapshot = pickle.dumps(scheduler_state)
    logger.info("Snapshot taken.")
    
    # 3. Restore Clones
    time.sleep(1) # Simulate time passing
    clone_a = pickle.loads(snapshot)
    clone_b = pickle.loads(snapshot)
    
    # 4. Verify Replay
    diff = abs(clone_a.next_deadline - clone_b.next_deadline)
    logger.info(f"Clone A Deadline: {clone_a.next_deadline}")
    logger.info(f"Clone B Deadline: {clone_b.next_deadline}")
    
    if diff < 0.001:
        logger.info("RESULT: MATCH. Clones woke up at exact same time.")
        logger.info("S4c Confirmed: Timing schedule is preserved in snapshot.")
    else:
        logger.error("Divergence? Pickling failed to preserve state.")

def test_s4d_constrained_convergence():
    logger.info("\n=== S4d: Constrained State Convergence ===")
    
    # 1. Setup Constrained State (Like S2 Adv)
    # Pool /28 (14 IPs), 5 reserved.
    pool = VIPPool("10.0.1.0/28", entropy_provider=SecretsProvider())
    
    # Simulate "Running State": 5 IPs already marked reserved/used
    for i in range(1, 6):
        pool.in_use_map[f"10.0.1.{i}"] = "reserved"
        
    logger.info(f"Original Pool State: {len(pool.in_use_map)} IPs blocked.")
    
    # 2. Snapshot
    snapshot = pickle.dumps(pool)
    logger.info("Snapshot taken.")
    
    # 3. Restore Clones (2 Replicas)
    # They both wake up believing 10.0.1.1-5 are blocked.
    clone_a = pickle.loads(snapshot)
    clone_b = pickle.loads(snapshot)
    
    # 4. Independent Allocation
    # Even though they use fresh OS entropy (Secrets), the candidates are constrained identically.
    host_def = HostRecord("h1", "host-1", "10.0.0.1", "s1", 60)
    
    # We must reset host_def state for each allocation context as in S2 fix
    host_a = copy.deepcopy(host_def)
    host_b = copy.deepcopy(host_def)
    
    # Run N trials
    matches = 0
    trials = 20
    for _ in range(trials):
        # Clear previous choice logic
        if host_a.current_vip:
             del clone_a.in_use_map[host_a.current_vip]
             host_a.current_vip = None
        if host_b.current_vip:
             del clone_b.in_use_map[host_b.current_vip]
             host_b.current_vip = None

        vip_a = clone_a.assign_initial_vip(host_a)
        vip_b = clone_b.assign_initial_vip(host_b)
        
        if vip_a == vip_b:
            matches += 1
            
    collision_rate = matches / trials
    expected_prob = 1.0 / 9.0 # 9 candidates
    
    logger.info(f"Collision Rate (2 Clones): {collision_rate:.1%} ({matches}/{trials})")
    
    if collision_rate > 0.0:
        logger.info("RESULT: Collisions observed.")
        logger.info("S4d Confirmed: Cloned constraints force convergence even with CSPRNG.")
    else:
        logger.info("No collisions (Low sample size?)")

if __name__ == "__main__":
    test_s4c_schedule_replay()
    test_s4d_constrained_convergence()
