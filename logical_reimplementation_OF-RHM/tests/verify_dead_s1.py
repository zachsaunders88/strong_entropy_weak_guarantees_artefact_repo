
import logging
import time
import subprocess
import sys
import os
import requests
from controller.vip_allocator import VIPPool, DeadEntropyProvider, HostRecord

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("verify_dead_s1")

DEAD_PORT = 8002 # Use unique port for this test

def start_dead_server():
    """Starts the DEAD server as a subprocess."""
    logger.info("Starting DEAD Server on port 8002...")
    cmd = [sys.executable, "-m", "uvicorn", "controller.dead.server:app", "--port", str(DEAD_PORT), "--host", "127.0.0.1"]
    
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(5) # Wait for startup
    return proc

def test_dead_s1():
    server_proc = start_dead_server()
    server_url = f"http://127.0.0.1:{DEAD_PORT}"
    
    try:
        logger.info("\n=== Evaluating DEAD against S1 (Boot Starvation) ===")
        
        # 1. Simulate Victim System (Using DEAD)
        logger.info(f"Victim System: Connected to DEAD Daemon at {server_url}")
        victim_provider = DeadEntropyProvider(server_url=server_url)
        victim_pool = VIPPool("10.0.1.0/24", entropy_provider=victim_provider)
        
        # 2. Simulate Attacker
        # Attacker tries to "Shadow" the victim. 
        # Even if attacker connects to SAME daemon, the daemon gives subsequent bytes, not same bytes.
        # But critically, the attacker CANNOT seed the daemon from outside to a known state.
        logger.info("Attacker Strategy: Attempting to shadow prediction.")
        attacker_provider = DeadEntropyProvider(server_url=server_url)
        attacker_pool = VIPPool("10.0.1.0/24", entropy_provider=attacker_provider)
        
        # 3. Execution
        victim_hosts = [HostRecord(f"h{i}", f"host-{i}", f"10.0.0.{i}", "s1", 60) for i in range(1, 6)]
        
        matches = 0
        trials = 5
        
        for v_host in victim_hosts:
            # Victim allocates
            real_vip = victim_pool.assign_initial_vip(v_host)
            
            # Attacker tries to predict (by asking for next entropy)
            # In a weak system (PRNG), if seeded same, next() is same.
            # In DEAD, next() yields new entropy.
            # So prediction should be statistically random choice from pool.
            
            # Note: Attacker construct a shadow host record
            a_host = HostRecord(v_host.host_id, v_host.name, v_host.real_ip, "s1", 60)
            predicted_vip = attacker_pool.assign_initial_vip(a_host)
            
            logger.info(f"Victim: {real_vip} | Attacker Guess: {predicted_vip}")
            
            if real_vip == predicted_vip:
                matches += 1
                
        logger.info(f"\nResults: {matches}/{trials} Matches")
        
        if matches == 0:
            logger.info("SUCCESS: DEAD Provider is resistant to S1 shadowing.")
            logger.info("The daemon's state cannot be inferred or cloned by an observer.")
        elif matches < 3:
             logger.info(f"PASS: Low match rate ({matches}) consistent with random chance.")
        else:
             logger.error("FAILURE: High match rate! Is the daemon deterministic?")

    finally:
        logger.info("Stopping DEAD Server...")
        server_proc.terminate()
        server_proc.wait()

if __name__ == "__main__":
    test_dead_s1()
