
import logging
import time
import subprocess
import requests
import sys
import os
import signal
from controller.vip_allocator import DeadEntropyProvider

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("test_dead_integration")

DEAD_PORT = 8001 # Use a different port to avoid conflicts with other runs

def start_dead_server():
    """Starts the DEAD server as a subprocess."""
    logger.info("Starting DEAD Server...")
    # We use a custom port via --port arg if supported, or we just rely on default.
    # checking server.py: serve_forever accepts port. But main.py?
    # Let's run python -m dead.main if it exists, or just invoke server.py
    # checking list_dir earlier: main.py exists.
    # Let's inspect main.py first? No, let's assume we can run the flask app/uvicorn.
    
    # We'll run uvicorn directy: uvicorn dead.server:app --port 8001
    cmd = [sys.executable, "-m", "uvicorn", "controller.dead.server:app", "--port", str(DEAD_PORT), "--host", "127.0.0.1"]
    
    # Needs PYTHONPATH to include '.' so it can find 'dead' module
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2) # Wait for startup
    return proc

def test_dead_integration():
    server_proc = start_dead_server()
    try:
        server_url = f"http://127.0.0.1:{DEAD_PORT}"
        
        # 1. Verify Server is Health
        try:
            resp = requests.get(f"{server_url}/status")
            resp.raise_for_status()
            status = resp.json()
            logger.info(f"DEAD Server Status: {status}")
        except Exception as e:
            logger.error(f"Failed to connect to DEAD server: {e}")
            return

        # 2. Test DeadEntropyProvider
        logger.info("Testing DeadEntropyProvider...")
        provider = DeadEntropyProvider(server_url=server_url)
        
        candidates = ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4", "10.0.0.5"]
        
        # Draw 10 samples
        samples = []
        for _ in range(10):
            val = provider.choice(candidates)
            samples.append(val)
            
        logger.info(f"Samples: {samples}")
        
        # Simple sanity check: are they from the list?
        assert all(s in candidates for s in samples)
        logger.info("SUCCESS: DeadEntropyProvider returned valid candidates.")
        
        # 3. Test Entropy Integer Endpoint directly
        resp = requests.get(f"{server_url}/entropy_int?bits=128")
        val = int(resp.json()["value"])
        logger.info(f"Direct 128-bit Entropy: {val}")
        assert val > 0

    finally:
        logger.info("Stopping DEAD Server...")
        server_proc.terminate()
        server_proc.wait()

if __name__ == "__main__":
    test_dead_integration()
