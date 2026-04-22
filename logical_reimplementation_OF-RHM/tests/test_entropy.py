import pytest
from controller.vip_allocator import StandardRandomProvider, SecretsProvider, VIPPool, EntropyProvider

def test_standard_random_provider():
    provider = StandardRandomProvider()
    choices = [1, 2, 3, 4, 5]
    # Simple smoke test: ensure it returns something from the list
    assert provider.choice(choices) in choices

def test_secrets_provider():
    provider = SecretsProvider()
    choices = [1, 2, 3, 4, 5]
    # Simple smoke test: ensure it returns something from the list
    assert provider.choice(choices) in choices

class MockEntropyProvider(EntropyProvider):
    def __init__(self, fixed_choice):
        self.fixed_choice = fixed_choice
    
    def choice(self, seq):
        if self.fixed_choice in seq:
            return self.fixed_choice
        return seq[0]

def test_vippool_custom_provider():
    # Create a pool with a Mock provider that always picks the last IP
    mock_provider = MockEntropyProvider("10.0.1.254")
    pool = VIPPool("10.0.1.0/24", entropy_provider=mock_provider)
    
    # We need a dummy host record to test allocation
    from controller.vip_allocator import HostRecord
    host = HostRecord("h1", "host1", "10.0.0.1", "s1", 60)
    
    # The pool should use our mock provider
    vip = pool.assign_initial_vip(host)
    assert vip == "10.0.1.254"
