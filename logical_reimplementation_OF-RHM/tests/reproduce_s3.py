
import time
import logging
import statistics
import threading
from controller.mutation import MutationScheduler
from controller.vip_allocator import VIPPool, HostRecord

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("reproduce_s3")

# We need to mock the VIPPool/Hosts slightly to avoid need for real allocation logic overhead
# but MutationScheduler relies on them.

class MockVIPPool(VIPPool):
    def __init__(self):
        # We don't care about the provider for Mock, but let's confirm environment
        self.reuse_timeout_s = 0
    def choose_new_vip(self, host, now=None):
        # Simulate the COST of generating a secure random number?
        # secrets.choice() is slightly slower than random.choice(), but negligible here.
        # But the point is the SCHEDULER loop timing.
        return "10.0.1.x" 

# Subclass to capture mutation times without running threads forever
class InstrumenthedScheduler(MutationScheduler):
    def __init__(self, vip_pool, hosts):
        super().__init__(vip_pool, hosts)
        self.mutation_events = [] # List of timestamps
        self.max_events = 10
        self.stop_event = threading.Event()

    def mutate_host(self, host: HostRecord):
        # Log the exact time of the mutation "decision"
        now = time.time()
        self.mutation_events.append(now)
        # Call original (mocked pool makes this cheap)
        try:
            super().mutate_host(host)
        except:
            pass
            
        if len(self.mutation_events) >= self.max_events:
            self.stop_event.set()

def test_s3_timing_leakage():
    logger.info("=== Reproducing S3: Schedule Leakage (Timing) ===")
    
    # Setup
    host = HostRecord("h1", "host-1", "10.0.0.1", "s1", mutation_interval_s=2)
    hosts = [host]
    pool = MockVIPPool()
    
    scheduler = InstrumenthedScheduler(pool, hosts)
    
    logger.info(f"Starting Scheduler with Interval={host.mutation_interval_s}s...")
    
    # We override the _loop in a way, or essentially rely on the thread.
    # The original _loop has a `time.sleep(1)`. This coarse sleep is actually part of the problem/behavior!
    # If the loop sleeps 1s, the check might drift or be jittery up to 1s. 
    # But usually it's "at least" interval.
    
    # Let's run it.
    scheduler.start()
    
    # Wait for 10 events
    # Interval is 2s, so 10 events = ~20s.
    logger.info("Collecting 10 mutation events (approx 20s)...")
    scheduler.stop_event.wait(timeout=30)
    scheduler.stop()
    
    timestamps = scheduler.mutation_events
    if len(timestamps) < 2:
        logger.error("Not enough events captured.")
        return

    logger.info(f"captured {len(timestamps)} events.")
    
    # Calculate intervals
    intervals = []
    for i in range(1, len(timestamps)):
        diff = timestamps[i] - timestamps[i-1]
        intervals.append(diff)
        
    avg_interval = statistics.mean(intervals)
    stdev_interval = statistics.stdev(intervals)
    
    logger.info(f"Intervals: {[f'{x:.4f}' for x in intervals]}")
    logger.info(f"Mean Interval: {avg_interval:.4f}s (Target: {host.mutation_interval_s}s)")
    logger.info(f"Standard Deviation (Jitter): {stdev_interval:.4f}s")
    
    # Analysis
    # In S3, we expect the intervals to be reasonably consistent (low jitter) OR predictable.
    # If standard deviation is low relative to the interval, the schedule is highly predictable.
    # An attacker can predict the next mutation time: T_next = T_last + Mean_Interval
    
    # Prediction Score (Brier-like concept, but simplified to "Error Margin")
    # Let's see how far off a naive "Add Mean" predictor would be.
    
    prediction_errors = []
    for i in range(1, len(timestamps)):
        # Predict based on previous timestamp + target interval (or learned mean)
        # Using target interval represents an attacker knowing the config.
        prediction = timestamps[i-1] + host.mutation_interval_s
        actual = timestamps[i]
        error = abs(actual - prediction)
        prediction_errors.append(error)
        
    avg_pred_error = statistics.mean(prediction_errors)
    logger.info(f"Avg Prediction Error (vs Ideal Schedule): {avg_pred_error:.4f}s")
    
    if avg_pred_error < 1.1: # Allow some slop for the sleep(1) loop
        logger.info("SUCCESS: Schedule is PREDICTABLE. Attacker knows when mutation happens within ~1s margin.")
        logger.info("This confirms Failure Mode S3: Timing is leaked.")
    else:
        logger.info("Schedule is noisy (Unpredictable).")

if __name__ == "__main__":
    test_s3_timing_leakage()
