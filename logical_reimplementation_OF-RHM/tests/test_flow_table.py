import pytest
import time
from gateway.flow_table import FlowTable, FlowKey, FlowEntry

@pytest.fixture
def flow_table():
    return FlowTable()

@pytest.fixture
def sample_key():
    return FlowKey("1.2.3.4", 12345, "10.0.0.1", 80, "TCP")

def test_insert_lookup(flow_table, sample_key):
    entry = FlowEntry(key=sample_key, dst_ip_to="192.168.1.1")
    flow_table.insert(entry)
    
    found = flow_table.lookup(sample_key)
    assert found is not None
    assert found.dst_ip_to == "192.168.1.1"
    
    # Lookup non-existent
    other_key = FlowKey("1.1.1.1", 1, "2.2.2.2", 2, "UDP")
    assert flow_table.lookup(other_key) is None

def test_expiry(flow_table, sample_key):
    entry = FlowEntry(key=sample_key, idle_timeout=1)
    flow_table.insert(entry)
    
    # Immediate lookup works
    assert flow_table.lookup(sample_key) is not None
    
    # Wait past timeout
    # We simulate time by passing 'now' to expire_old
    start_time = entry.last_seen
    
    # Not expired yet
    removed = flow_table.expire_old(now=start_time + 0.5)
    assert removed == 0
    assert flow_table.lookup(sample_key) is not None
    
    # Expired
    removed = flow_table.expire_old(now=start_time + 1.5)
    assert removed == 1
    
    # Should be gone
    # Note: lookup updates last_seen? 
    # If we called lookup above at +0.5, last_seen would be updated to +0.5 (real time) or whatever time.time() is.
    # In our mock test, we rely on expire_old using the passed 'now'.
    # But lookup() uses real time.time() to update last_seen! 
    # This makes testing tricky if we mix real sleep and mock time.
    # Let's just check internal table directly or ensure we don't call lookup in between if we want strict control.
    # Actually, lookup() updating last_seen is a side effect.
    
    # Let's verify it's gone from the dict
    assert sample_key not in flow_table._table

def test_concurrency_smoke(flow_table):
    # Basic smoke test for locking
    import threading
    
    def worker():
        for i in range(100):
            key = FlowKey(f"src{i}", i, "dst", 80, "TCP")
            flow_table.insert(FlowEntry(key=key))
            flow_table.lookup(key)
            
    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
        
    assert len(flow_table.get_all()) == 100
