import subprocess
import time
import sys
import os
import signal
import requests
from contextlib import contextmanager

# Paths
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
PYTHON = sys.executable
PYTHON_ARGS = [PYTHON, "-u"]

class ProcessManager:
    def __init__(self):
        self.processes = []

    def start_process(self, cmd, name, cwd=PROJECT_ROOT, env=None):
        print(f"Starting {name}...")
        if env is None:
            env = os.environ.copy()
        
        # Add project root to PYTHONPATH
        env["PYTHONPATH"] = PROJECT_ROOT
        
        out_file = open(f"{name}.log", "w")
        
        p = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=out_file,
            stderr=subprocess.STDOUT, # Merge stderr to stdout
            text=True
        )
        self.processes.append((name, p, out_file))
        return p

    def stop_all(self):
        print("Stopping all processes...")
        for name, p, f in reversed(self.processes):
            if p.poll() is None:
                print(f"Terminating {name}...")
                p.terminate()
                try:
                    p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    p.kill()
            else:
                print(f"{name} already exited with code {p.returncode}")
            
            f.close()
            
            # Print log content if failed
            if p.returncode != 0 and p.returncode is not None:
                print(f"[{name} LOG]")
                with open(f"{name}.log", "r") as lf:
                    print(lf.read())

@contextmanager
def e2e_environment():
    pm = ProcessManager()
    try:
        # Start Controller
        pm.start_process(
            [PYTHON, "-m", "controller", "--config", "configs/dev.yaml"],
            "Controller"
        )
        
        # Start Gateway
        pm.start_process(
            [PYTHON, "-m", "gateway", "--config", "configs/dev.yaml"],
            "Gateway"
        )
        
        # Start Server (on port 8000 as per dev.yaml rIPs? No, dev.yaml has 10.0.0.x)
        # We need the server to bind to the rIP? 
        # On Windows/Localhost, we can't bind to 10.0.0.x easily.
        # Workaround: 
        # 1. Configure Controller to use 127.0.0.1 for rIPs in a special e2e config.
        # 2. Start Server on 127.0.0.1:8000.
        
        # Let's create a temporary config for E2E?
        # Or just use a specific config file.
        
        yield pm
    finally:
        pm.stop_all()
