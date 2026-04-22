import pytest
import time
from controller.mutation import MutationScheduler
from controller.vip_allocator import HostRecord, VIPPool

def test_mutation_scheduler():
    pool = VIPPool("10.0.1.0/24")
    # Fast mutation for test
    host = HostRecord("h1", "host-1", "10.0.0.1", "s1", mutation_interval_s=2)
    pool.assign_initial_vip(host)
    initial_vip = host.current_vip
    
    scheduler = MutationScheduler(pool, [host])
    scheduler.start()
    
    try:
        # Wait for mutation (interval 2s, check loop 1s)
        time.sleep(3.5)
        
        new_vip = host.current_vip
        assert new_vip != initial_vip
        assert len(host.history) >= 2 # Initial + 1 mutation
        
    finally:
        scheduler.stop()
