
import logging
import time
import subprocess
import sys
import os
import statistics
import threading
from controller.mutation import MutationScheduler
from controller.vip_allocator import VIPPool, DeadEntropyProvider, HostRecord

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("verify_dead_s3")

DEAD_PORT = 8006

def start_dead_server():
    logger.info(f"Starting DEAD Server on port {DEAD_PORT}...")
    cmd = [sys.executable, "-m", "uvicorn", "controller.dead.server:app", "--port", str(DEAD_PORT), "--host", "127.0.0.1"]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(5)
    return proc

class InstrumenthedScheduler(MutationScheduler):
    def __init__(self, vip_pool, hosts):
        super().__init__(vip_pool, hosts)
        self.mutation_events = [] 
        self.max_events = 5
        self.stop_event = threading.Event()

    def mutate_host(self, host: HostRecord):
        now = time.time()
        self.mutation_events.append(now)
        try:
            # We skip actual API/Gateway calls by mocking logic or expecting failure?
            # MutationScheduler calls 'logic.update_gateway_flow' -> 'gateway_client.update_flow'.
            # We need to mock gateway client to avoid errors slowing down loop.
            # But wait, MutationScheduler in this codebase calls `self.vip_pool.choose_new_vip`
            # then calls `self.app_logic.update_mappings`.
            # We need to mock `app_logic`.
            pass
        except:
            pass
        
        # We perform the IP selection to exercise DEAD provider
        try:
            vip = self.vip_pool.choose_new_vip(host)
            logger.info(f"Mutated {host.name} -> {vip}")
        except Exception as e:
            logger.warning(f"Mutation Failed: {e}")

        if len(self.mutation_events) >= self.max_events:
            self.stop_event.set()

def test_dead_s3():
    server_proc = start_dead_server()
    server_url = f"http://127.0.0.1:{DEAD_PORT}"
    
    try:
        logger.info("\n=== Evaluating DEAD against S3 (Timing Leakage) ===")
        
        # 1. Setup
        # We use a short interval (2s) to capture events quickly
        host = HostRecord("h1", "host-1", "10.0.0.1", "s1", mutation_interval_s=2)
        hosts = [host]
        
        # Use DEAD Provider
        provider = DeadEntropyProvider(server_url=server_url)
        pool = VIPPool("10.0.1.0/24", entropy_provider=provider)
        
        # Mock Logic/App
        # The scheduler accepts `vip_pool` and `hosts`.
        # It assumes `from controller.app import logic` is available if it calls generic mutate?
        # Check MutationScheduler code ideally. 
        # But here we override mutate_host entirely in InstrumentedScheduler, so it's fine.
        
        scheduler = InstrumenthedScheduler(pool, hosts)
        
        logger.info("Starting Scheduler...")
        scheduler.start()
        
        logger.info("Collecting 5 events...")
        scheduler.stop_event.wait(timeout=20)
        scheduler.stop()
        
        timestamps = scheduler.mutation_events
        if len(timestamps) < 2:
            logger.error("Not enough events.")
            return

        intervals = []
        for i in range(1, len(timestamps)):
            intervals.append(timestamps[i] - timestamps[i-1])
            
        avg_interval = statistics.mean(intervals)
        stdev = statistics.stdev(intervals) if len(intervals) > 1 else 0
        
        logger.info(f"Intervals: {[f'{x:.2f}' for x in intervals]}")
        logger.info(f"Mean: {avg_interval:.2f}s (Target: 2.0s)")
        logger.info(f"StDev (Jitter): {stdev:.2f}s")
        
        # S3 Failure Condition: Jitter is low (Predictable)
        # S3 Pass Condition: Jitter is high (Unpredictable)
        
        # S3 Failure Condition: Jitter is low (Predictable)
        # S3 Pass Condition: Jitter is high (Unpredictable)
        
        if stdev < 0.1:
            logger.info("RESULT: High Predictability (Low Jitter).")
            logger.info("DEAD integration did NOT fix S3 (Timing Leakage) because scheduling logic is unchanged.")
        else:
            logger.info("RESULT: Low Predictability (High Jitter).")
            logger.info("SUCCESS: DEAD integration mitigated S3 (Timing Leakage).")

    finally:
        server_proc.terminate()
        server_proc.wait()

if __name__ == "__main__":
    test_dead_s3()
