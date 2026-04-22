"""Shared utilities for experiment scripts."""

import math
import platform
import sys


def get_reproducibility_metadata():
    """Return environment metadata for inclusion in results JSON."""
    meta = {
        'python_version': sys.version,
        'platform': platform.platform(),
        'machine': platform.machine(),
    }
    try:
        import scipy
        meta['scipy_version'] = scipy.__version__
    except ImportError:
        meta['scipy_version'] = None
    return meta


def wilson_ci(p, n, z=1.96):
    """Wilson score confidence interval for proportion p with n observations."""
    if n == 0:
        return 0.0, 0.0
    denom = 1 + z ** 2 / n
    centre = p + z ** 2 / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2))
    ci_low = (centre - margin) / denom
    ci_high = (centre + margin) / denom
    return ci_low, ci_high


def birthday_bound(k, n):
    """Theoretical Birthday Paradox collision probability for k draws from n items."""
    if k > n:
        return 1.0
    p_no_collision = 1.0
    for i in range(k):
        p_no_collision *= (n - i) / n
    return 1.0 - p_no_collision
