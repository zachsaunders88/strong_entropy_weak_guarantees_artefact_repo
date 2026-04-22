"""Entropy Mixing Network (EMN).

Re-exports EntropyMixingNetwork from the logical implementation.
Source: logical_reimplementation_OF-RHM/controller/dead/emn.py

The EMN is a cryptographic state machine whose security reduces to the
established properties of HKDF (RFC 5869) and HMAC-SHA256 (RFC 2104).
It maintains a 32-byte internal state and produces output through a
forward-secure, personalisable derivation function.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', 'logical_reimplementation_OF-RHM'))

from controller.dead.emn import EntropyMixingNetwork

__all__ = ['EntropyMixingNetwork']
