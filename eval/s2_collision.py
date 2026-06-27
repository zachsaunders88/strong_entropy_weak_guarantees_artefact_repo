#!/usr/bin/env python3
"""Reproduces Table 2: S2 cross-replica collision experiment.

Runs ten independent 1,000-step collision experiments (10,000 steps total) per
condition at k=5, n=9 on a constrained /28 pool:
  - PRNG  (StandardRandomProvider, no coordination)
  - CSPRNG (SecretsProvider / os.urandom, no coordination)
  - DEAD v1.0 (CSPRNG-quality entropy, no epoch-key coordination)
  - DEAD v1.1 (epoch-key coordinated)

Expected output (Table 2, constrained /28 pool, k=5, n=9, grand means):
  PRNG: ~74.8%  CSPRNG: ~74.1%  DEAD v1.0: ~74.8%  DEAD v1.1: 0.0% (0/10,000)

Run from the artifact_repo root:
    python eval/s2_collision.py
"""
import subprocess
import sys
import os

_LOGICAL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '..', 'logical_reimplementation_OF-RHM')
_SCRIPT = os.path.join(_LOGICAL, 'tests', 's2_extended_runs.py')


if __name__ == '__main__':
    result = subprocess.run(
        [sys.executable, _SCRIPT],
        cwd=_LOGICAL,
        check=False
    )
    sys.exit(result.returncode)
