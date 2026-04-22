import pytest
import time
from controller.vip_allocator import HostRecord, VIPPool

@pytest.fixture
def small_pool():
    # A small pool with only a few IPs: 10.0.1.1 to 10.0.1.6 (approx)
    return VIPPool("10.0.1.0/29", reuse_timeout_s=10)

@pytest.fixture
def host_a():
    return HostRecord("h1", "host-a", "10.0.0.1", "s1", 30)

@pytest.fixture
def host_b():
    return HostRecord("h2", "host-b", "10.0.0.2", "s1", 30)

def test_initial_assignment(small_pool, host_a):
    vip = small_pool.assign_initial_vip(host_a)
    assert vip in small_pool.available_vips
    assert host_a.current_vip == vip
    assert small_pool.in_use_map[vip] == host_a.host_id

def test_no_collision(small_pool, host_a, host_b):
    vip_a = small_pool.assign_initial_vip(host_a)
    vip_b = small_pool.assign_initial_vip(host_b)
    assert vip_a != vip_b

def test_reuse_timeout(small_pool, host_a):
    # Force deterministic time
    start_time = 1000.0
    
    # 1. Assign first vIP
    vip1 = small_pool.choose_new_vip(host_a, now=start_time)
    
    # 2. Mutate immediately (release vip1)
    vip2 = small_pool.choose_new_vip(host_a, now=start_time + 1)
    assert vip1 != vip2
    
    # 3. Verify vip1 is blocked
    assert small_pool._is_recently_used(host_a.host_id, vip1, start_time + 5) == True
    
    # 4. Assign a new vIP (should succeed and not be vip1)
    vip3 = small_pool.choose_new_vip(host_a, now=start_time + 5)
    assert vip3 != vip1
    assert vip3 != vip2
    
    # 5. Advance time past timeout (1001 + 10 = 1011). At 1012 vip1 should be free.
    assert small_pool._is_recently_used(host_a.host_id, vip1, start_time + 12) == False

def test_exhaustion(small_pool, host_a):
    # Pool 10.0.1.0/29 has 6 usable IPs (.1 to .6)
    # Occupy all
    hosts = [HostRecord(f"h{i}", f"host-{i}", f"10.0.0.{i}", "s1", 30) for i in range(6)]
    for h in hosts:
        small_pool.assign_initial_vip(h)
        
    # Try to add one more
    extra = HostRecord("extra", "extra", "10.0.0.99", "s1", 30)
    with pytest.raises(RuntimeError):
        small_pool.assign_initial_vip(extra)
