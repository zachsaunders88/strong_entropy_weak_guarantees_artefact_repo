#!/usr/bin/env python3
"""Reproduces Table 2: S2 cross-replica collision experiment.

Runs 500-step collision experiments across three independent runs for:
  - PRNG  (WeakBootProvider, fixed seed)
  - CSPRNG (SecretsProvider / os.urandom)
  - DEAD v1.0 (uncoordinated, no epoch key)
  - DEAD v1.1 (epoch-key coordinated)

Expected output (Table 2, constrained /28 pool, k=5, n=9):
  PRNG: ~74.9%  CSPRNG: ~76.1%  DEAD v1.0: ~74.1%  DEAD v1.1: 0.0%

Run from the artifact_repo root:
    python eval/s2_collision.py
"""
import subprocess
import sys
import os

_LOGICAL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '..', 'logical_reimplementation_OF-RHM')
_SCRIPT = os.path.join(_LOGICAL, 'tests', 'reproduce_s2_advanced.py')


if __name__ == '__main__':
    result = subprocess.run(
        [sys.executable, _SCRIPT],
        cwd=_LOGICAL,
        check=False
    )
    sys.exit(result.returncode)
