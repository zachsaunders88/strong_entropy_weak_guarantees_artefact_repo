import pytest
from controller.logic import ControllerLogic, FlowActions
from controller.vip_allocator import HostRecord, VIPPool

@pytest.fixture
def logic_setup():
    pool = VIPPool("10.0.1.0/24")
    host = HostRecord("h1", "host-1", "10.0.0.1", "s1", 60)
    pool.assign_initial_vip(host)
    vip = host.current_vip
    
    logic = ControllerLogic(pool, [host])
    return logic, host, vip

def test_packet_in_vip_hit(logic_setup):
    logic, host, vip = logic_setup
    
    # Client -> vIP
    actions = logic.handle_packet_in("1.2.3.4", vip)
    
    assert actions.allow is True
    assert actions.inbound_dst_rewrite == host.real_ip
    assert actions.outbound_src_rewrite == vip

def test_packet_in_rip_deny(logic_setup):
    logic, host, vip = logic_setup
    
    # Client -> rIP (should deny by default)
    actions = logic.handle_packet_in("1.2.3.4", host.real_ip)
    
    assert actions.allow is False

def test_packet_in_rip_allow_admin(logic_setup):
    logic, host, vip = logic_setup
    
    admin_ip = "9.9.9.9"
    logic.add_admin_ip(admin_ip)
    
    # Admin -> rIP
    actions = logic.handle_packet_in(admin_ip, host.real_ip)
    
    assert actions.allow is True
    assert actions.inbound_dst_rewrite is None # No translation needed

def test_dns_resolve(logic_setup):
    logic, host, vip = logic_setup
    
    resolved = logic.resolve_dns("host-1")
    assert resolved == vip
    
    assert logic.resolve_dns("unknown") is None
