
import logging
import time
import statistics
import threading
from controller.dead.reseeder import Reseeder
from controller.dead.emn import EntropyMixingNetwork

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("verify_dead_s5")

def test_reseed_predictability():
    logger.info("=== Evaluating Failure Mode S5: Predictable Reseeding ===")
    
    # 1. Simulate "Traditional" behavior (Fixed Interval Reseeding)
    # Many systems just reseed every X seconds or on access count.
    logger.info("\n--- Scenario A: Fixed Interval (Traditional) ---")
    emn_a = EntropyMixingNetwork()
    
    # Configure Reseeder with NO jitter
    reseeder_a = Reseeder(
        emn_a, 
        periodic_seconds=0.2,       # Fast for test (200ms)
        jitter_max_seconds=0.0      # NO JITTER
    )
    
    logger.info("Collecting reseed events for 2 seconds...")
    reseeder_a.start_periodic()
    time.sleep(2.5)
    reseeder_a.stop_periodic()
    
    intervals_a = reseeder_a.observed_intervals
    if len(intervals_a) > 1:
        stdev_a = statistics.stdev(intervals_a)
        mean_a = statistics.mean(intervals_a)
        logger.info(f"Fixed Intervals: {[f'{x:.4f}' for x in intervals_a[:5]]}...")
        logger.info(f"Jitter (StDev): {stdev_a:.6f}s (Target: 0.0s)")
        
        if stdev_a < 0.05:
            logger.info(">> VULNERABILITY CONFIRMED: Reseed timing is highly predictable.")
            logger.info("   Attacker knows EXACTLY when state refresh occurs.")
    else:
        logger.warning("Not enough events for A.")


    # 2. Simulate DEAD behavior (Jittered Reseeding)
    logger.info("\n--- Scenario B: DEAD Reseeder (Jitter Enabled) ---")
    emn_b = EntropyMixingNetwork()
    
    # Configure Reseeder with JITTER (20% of period)
    reseeder_b = Reseeder(
        emn_b, 
        periodic_seconds=0.2,
        jitter_frac=0.5             # 50% Jitter (0.2s + rand(0..0.1s))
    )
    
    logger.info("Collecting reseed events for 2 seconds...")
    reseeder_b.start_periodic()
    time.sleep(2.5)
    reseeder_b.stop_periodic()
    
    intervals_b = reseeder_b.observed_intervals
    if len(intervals_b) > 1:
        stdev_b = statistics.stdev(intervals_b)
        mean_b = statistics.mean(intervals_b)
        logger.info(f"Jittered Intervals: {[f'{x:.4f}' for x in intervals_b[:5]]}...")
        logger.info(f"Jitter (StDev): {stdev_b:.6f}s")
        
        if stdev_b > 0.02: # Significant variance
            logger.info(">> MITIGATION CONFIRMED: Reseed timing is unpredictable.")
            logger.info("   Attacker is uncertain about state validity window.")
    else:
         logger.warning("Not enough events for B.")

if __name__ == "__main__":
    test_reseed_predictability()
