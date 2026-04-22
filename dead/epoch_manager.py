"""EpochManager — cross-replica coordination primitive (S2 mitigation).

Re-exports EpochManager from the logical implementation.
Source: logical_reimplementation_OF-RHM/controller/dead/server.py

The EpochManager provides time-windowed epoch keys: all replicas querying
/epoch_key with the same scope string within a 30-second window receive the
same 128-bit key. Each replica then performs an identical Fisher-Yates shuffle
of the candidate pool and selects the address at its replica_id slot,
guaranteeing distinct assignments without direct inter-replica communication.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', 'logical_reimplementation_OF-RHM'))

from controller.dead.server import EpochManager

__all__ = ['EpochManager']
