
import logging
import math
import time
import subprocess
import sys
import os
import collections
import copy
from controller.vip_allocator import VIPPool, DeadEntropyProvider, HostRecord

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("verify_dead_s2_coordination")

DEAD_PORT = 8005

def start_dead_server():
    logger.info(f"Starting DEAD Server on port {DEAD_PORT}...")
    cmd = [sys.executable, "-m", "uvicorn", "controller.dead.server:app", "--port", str(DEAD_PORT), "--host", "127.0.0.1"]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(5)
    return proc

def test_dead_s2_coordination():
    server_proc = start_dead_server()
    server_url = f"http://127.0.0.1:{DEAD_PORT}"
    
    try:
        logger.info("\n=== Evaluating DEAD S2 Mitigation (Coordination) ===")
        
        pool_cidr = "10.0.1.0/28" # 14 usable
        num_replicas = 5
        num_steps = 500  # Increased from 20 to 500 for the ESORICS 2026 evaluation
        scope = "test-scope-s2"
        
        logger.info(f"Config: Pool={pool_cidr}, Replicas={num_replicas}, Scope={scope}")
        
        # Initialize Replicas with Unique IDs and Shared Scope
        replicas = []
        for i in range(num_replicas):
            r_id = str(i) # Use numeric ID for deterministic slot separation
            provider = DeadEntropyProvider(server_url=server_url)
            pool = VIPPool(pool_cidr, 
                           entropy_provider=provider, 
                           replica_id=r_id, 
                           coordination_scope=scope)
            
            # Constraint: First 5 IPs reserved (Same as S2 Adv)
            for j in range(1, 6):
                pool.in_use_map[f"10.0.1.{j}"] = "reserved"
            replicas.append(pool)
            
        host_def = HostRecord("h1", "host-1", "10.0.0.1", "s1", 60)
        
        steps_with_collision = 0
        all_choices = []
        
        for step in range(num_steps):
            step_choices = []
            
            # We must simulate that time passes or force a new epoch?
            # Epoch manager rotates every 30s.
            # If we run fast, we use SAME epoch key.
            # This is GOOD. Same epoch key = Same Shuffle.
            # Unique Replica ID = Unique Slot.
            # So within one epoch, they should NEVER collide.
            
            for pool in replicas:
                # Reset host context
                for v, h_id in list(pool.in_use_map.items()):
                    if h_id == host_def.host_id:
                        del pool.in_use_map[v]
                
                h_clone = copy.copy(host_def)
                h_clone.current_vip = None 
                
                # Note: choose_new_vip contains the logic, assign_initial_vip might not have been updated?
                # I updated choose_new_vip. I must ensure I call that or update assign_initial.
                # Let's call choose_new_vip.
                vip = pool.choose_new_vip(h_clone)
                step_choices.append(vip)
            
            counts = collections.Counter(step_choices)
            if any(c > 1 for c in counts.values()):
                steps_with_collision += 1
                logger.warning(f"Collision in step {step}: {step_choices}")
            
            all_choices.extend(step_choices)
            
        collision_prob = steps_with_collision / num_steps
        logger.info(f"Collision Rate: {collision_prob:.1%} ({steps_with_collision}/{num_steps})")
        
        if collision_prob == 0.0:
            logger.info("SUCCESS: Coordination Logic Eliminated Collisions!")
            logger.info("Epoch Key provided shared permutation, Replica ID provided unique slot.")
        else:
            logger.error("FAILURE: Collisions persisted.")

        collision_rate = steps_with_collision / num_steps
        if steps_with_collision == 0:
            ci_upper = 1 - (0.05 ** (1 / num_steps))
            print(f"Collision rate: 0.0% (0/{num_steps})")
            print(f"95% upper confidence bound (rule of three): {ci_upper*100:.2f}%")
            print(f"Epoch key coordination bounds true rate below {ci_upper*100:.2f}%")
        else:
            z = 1.96
            n = num_steps
            p = collision_rate
            denom   = 1 + z**2 / n
            centre  = p + z**2 / (2 * n)
            margin  = z * math.sqrt(p * (1-p) / n + z**2 / (4 * n**2))
            ci_low  = (centre - margin) / denom
            ci_high = (centre + margin) / denom
            print(f"Collision rate: {collision_rate*100:.1f}% ({steps_with_collision}/{num_steps})")
            print(f"95% Wilson CI: [{ci_low*100:.1f}%, {ci_high*100:.1f}%]")

    finally:
        server_proc.terminate()
        server_proc.wait()

if __name__ == "__main__":
    test_dead_s2_coordination()
