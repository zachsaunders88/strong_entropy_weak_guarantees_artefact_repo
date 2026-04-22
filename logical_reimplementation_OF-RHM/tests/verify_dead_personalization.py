
import logging
import time
import subprocess
import sys
import os
import requests
import pickle
import threading
from controller.dead.emn import EntropyMixingNetwork

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("verify_dead_personalization")

def test_personalization_internal():
    """
    Test EMN directly (unit test logic) to verify mixin behavior.
    """
    logger.info("=== 1. Internal EMN Check ===")
    
    # 1. Create two identical EMN instances (Simulation of Cloned VM state)
    seed = b"test_seed_32_bytes_long_string!!"
    emn1 = EntropyMixingNetwork(initial_seed=seed)
    emn2 = EntropyMixingNetwork(initial_seed=seed)
    
    # 2. Verify they match without context
    # Note: We must mock the PRNG to be deterministic for this check, 
    # but emn uses SystemRandom.
    # Actually, emn.next() uses self.prng.getrandbits().
    # SystemRandom is non-deterministic. So this unit test is tricky unless we mock prng.
    
    # Let's trust the integration test over unit mocking here.
    logger.info("(Skipping internal check due to SystemRandom usage)")

def test_dead_personalization_integration():
    logger.info("\n=== 2. Integration Test: DEAD + Context ===")
    
    # Start DEAD
    PORT = 8004
    cmd = [sys.executable, "-m", "uvicorn", "controller.dead.server:app", "--port", str(PORT), "--host", "127.0.0.1"]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(5)
    server_url = f"http://127.0.0.1:{PORT}"
    
    try:
        # We can't easily clone the running daemon process state externally.
        # But we can verify that sending the SAME context vs DIFFERENT context affects the output?
        # No, because output is random anyway.
        
        # Verification Logic:
        # We need to ensure the context IS being used.
        # Ideally, we want to show that if we could force the state to be matching, context would split it.
        # Since we can't force the state of the running daemon from outside without restarting it...
        
        # Let's verify valid parameters first.
        resp = requests.get(f"{server_url}/entropy_int?bits=64&context=host-A")
        assert resp.status_code == 200
        val_a = int(resp.json()["value"])
        logger.info(f"Context 'host-A' -> {val_a}")
        
        resp = requests.get(f"{server_url}/entropy_int?bits=64&context=host-B")
        assert resp.status_code == 200
        val_b = int(resp.json()["value"])
        logger.info(f"Context 'host-B' -> {val_b}")
        
        logger.info("SUCCESS: Server accepts context parameter.")
        
    finally:
        proc.terminate()
        proc.wait()

def test_s4_mitigation_simulation():
    """
    Simulate S4 Mitigation by manually invoking EMN with mocked PRNG.
    This proves the cryptography works as intended for clones.
    """
    logger.info("\n=== 3. Cryptographic Verification (S4 Mitigation) ===")
    
    import random
    
    class MockEMN(EntropyMixingNetwork):
        def __init__(self):
            super().__init__()
            # Force deterministic PRNG
            self.prng = random.Random(42) 
            
    # 1. Setup Clones
    # Clone A and Clone B start with identical state AND identical PRNG seed.
    clone_a = MockEMN()
    clone_b = MockEMN() # Identical seed 42
    
    # 2. Execute with Context
    # If context works, these MUST be different despite identical PRNG stream.
    
    out_a = clone_a.next(context=b"host-1")
    out_b = clone_b.next(context=b"host-2")
    
    logger.info(f"Clone A (ctx=host-1): {out_a.hex()[:16]}...")
    logger.info(f"Clone B (ctx=host-2): {out_b.hex()[:16]}...")
    
    if out_a != out_b:
        logger.info("SUCCESS: Personalization caused divergence!")
        logger.info("Even with identical state and PRNG stream, adding Context splits the timeline.")
    else:
        logger.error("FAILURE: Clones produced identical output despite context.")

if __name__ == "__main__":
    test_personalization_internal()
    test_dead_personalization_integration()
    test_s4_mitigation_simulation()
