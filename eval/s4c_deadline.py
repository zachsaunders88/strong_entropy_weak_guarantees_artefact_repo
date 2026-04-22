#!/usr/bin/env python3
"""Reproduces Table 4 S4c row: clone deadline divergence experiment.

Runs the 200-trial S4c schedule-divergence experiment in the logical
reimplementation and prints summary statistics.

Run from the repository root:
  python eval/s4c_deadline.py
"""

import os
import subprocess
import sys

_LOGICAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'logical_reimplementation_OF-RHM')
_SCRIPT = os.path.join(_LOGICAL, 'tests', 'verify_dead_s4c_multitrial.py')


if __name__ == '__main__':
    result = subprocess.run([sys.executable, _SCRIPT], cwd=_LOGICAL, check=False)
    raise SystemExit(result.returncode)
