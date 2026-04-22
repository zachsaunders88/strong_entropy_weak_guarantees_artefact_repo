"""EntropyProvider classes.

Re-exports provider hierarchy from the logical implementation.
Source: logical_reimplementation_OF-RHM/controller/vip_allocator.py

Provider hierarchy (weakest to strongest):
  StandardRandomProvider  — stdlib random.choice(); baseline/attack surface
  SecretsProvider         — secrets / os.urandom; CSPRNG-level quality
  DeadEntropyProvider     — DEAD daemon; full governance (S1-S5 mitigations)

DeadEntropyProvider fails closed: raises RuntimeError on any HTTP failure
rather than falling back to a weaker source, preventing S5 (silent degradation).
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', 'logical_reimplementation_OF-RHM'))

from controller.vip_allocator import (
    StandardRandomProvider,
    SecretsProvider,
    DeadEntropyProvider,
)

__all__ = ['StandardRandomProvider', 'SecretsProvider', 'DeadEntropyProvider']
