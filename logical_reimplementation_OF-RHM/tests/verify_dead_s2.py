
import logging
import time
import subprocess
import sys
import os
import collections
import copy
from controller.vip_allocator import VIPPool, DeadEntropyProvider, HostRecord

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("verify_dead_s2")

DEAD_PORT = 8003 # Unique port for S2 test

def start_dead_server():
    """Starts the DEAD server."""
    logger.info(f"Starting DEAD Server on port {DEAD_PORT}...")
    cmd = [sys.executable, "-m", "uvicorn", "controller.dead.server:app", "--port", str(DEAD_PORT), "--host", "127.0.0.1"]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    return proc

def test_dead_s2():
    server_proc = start_dead_server()
    server_url = f"http://127.0.0.1:{DEAD_PORT}"
    
    try:
        logger.info("\n=== Evaluating DEAD against S2 (Cross-Replica Correlation) ===")
        
        # 1. Configuration (S2 Advanced)
        pool_cidr = "10.0.1.0/28" # 14 usable IPs
        num_replicas = 5
        num_steps = 20
        
        logger.info(f"Config: Pool={pool_cidr}, Replicas={num_replicas}")
        logger.info("Using DeadEntropyProvider (Daemon-sourced random)")

        # 2. Initialize Replicas with Shared Constraint
        replicas = []
        for _ in range(num_replicas):
            # Each replica connects to the SAME daemon (typical sidecar or central service)
            # OR separate daemons. Use SAME URL to simulate multiple controllers sharing one source (or just valid CSPRNGs)
            provider = DeadEntropyProvider(server_url=server_url)
            pool = VIPPool(pool_cidr, entropy_provider=provider)
            
            # Constraint: First 5 IPs reserved
            for i in range(1, 6):
                pool.in_use_map[f"10.0.1.{i}"] = "reserved"
            replicas.append(pool)
            
        # 3. Execution (Birthday Paradox)
        host_def = HostRecord("h1", "host-1", "10.0.0.1", "s1", 60)
        
        steps_with_collision = 0
        all_choices = []
        
        for step in range(num_steps):
            step_choices = []
            for pool in replicas:
                # Reset host state context for this replica check
                # Note: VIPPool modifies in_use_map. We must clear previous allocations for h1 if present.
                for v, h_id in list(pool.in_use_map.items()):
                    if h_id == host_def.host_id:
                        del pool.in_use_map[v]
                
                # Clone host record to avoid side-effects across replicas in this logical simulation loop
                h_clone = copy.copy(host_def)
                h_clone.current_vip = None 
                
                vip = pool.assign_initial_vip(h_clone)
                step_choices.append(vip)
            
            # Check Collision
            counts = collections.Counter(step_choices)
            if any(c > 1 for c in counts.values()):
                steps_with_collision += 1
            
            all_choices.extend(step_choices)
            
        collision_prob = steps_with_collision / num_steps
        logger.info(f"Collision Rate: {collision_prob:.1%} ({steps_with_collision}/{num_steps})")
        
        # 9 candidates, 5 picks => ~74% collision expected
        expected_prob = 1.0 - (9 * 8 * 7 * 6 * 5) / (9**5)
        
        if collision_prob > 0.5:
            logger.info("RESULT: High Collision Rate Confirmed.")
            logger.info("Conclusion: DEAD (Perfect Entropy) does NOT fix Systemic Correlation (S2).")
            logger.info("Shared constraints force collisions regardless of entropy quality.")
        else:
            logger.warning("RESULT: Low collision rate (unexpected for S2 parameters).")

    except Exception as e:
        logger.error(f"Test Failed: {e}")
    finally:
        logger.info("Stopping DEAD Server...")
        server_proc.terminate()
        server_proc.wait()

if __name__ == "__main__":
    test_dead_s2()
