
import logging
import math
import secrets
import collections
import time
import subprocess
import sys
import os

# Ensure logical_reimplementation_OF-RHM/ is on sys.path when running this file directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from controller.vip_allocator import VIPPool, HostRecord, SecretsProvider, StandardRandomProvider, DeadEntropyProvider

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("reproduce_s2_advanced")

NUM_REPLICAS = 5
EFFECTIVE_POOL_SIZE = 9  # /28 pool, 5 IPs reserved
DEAD_V1_PORT = 8010


def start_dead_server(port):
    logger.info(f"Starting DEAD Server on port {port}...")
    cmd = [sys.executable, "-m", "uvicorn", "controller.dead.server:app", "--port", str(port), "--host", "127.0.0.1"]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(5)
    return proc

def _run_condition(pool_cidr, num_replicas, num_steps, pool_factory, label):
    """Run one S2 condition and return (steps_with_collision, num_steps)."""
    replicas = []
    for replica_idx in range(num_replicas):
        pool = pool_factory(replica_idx)
        for i in range(1, 6):
            pool.in_use_map[f"10.0.1.{i}"] = "reserved"
        replicas.append(pool)

    host_def = HostRecord("h1", "host-1", "10.0.0.1", "s1", 60)
    steps_with_collision = 0
    all_choices = []

    for step in range(num_steps):
        step_choices = []
        for pool in replicas:
            for v, h_id in list(pool.in_use_map.items()):
                if h_id == host_def.host_id:
                    del pool.in_use_map[v]
            host_def.current_vip = None
            vip = pool.assign_initial_vip(host_def)
            step_choices.append(vip)

        counts = collections.Counter(step_choices)
        if any(count > 1 for count in counts.values()):
            steps_with_collision += 1
        all_choices.extend(step_choices)

    collision_rate = steps_with_collision / num_steps
    z = 1.96
    n = num_steps
    p = collision_rate
    denom  = 1 + z**2 / n
    centre = p + z**2 / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
    ci_low  = (centre - margin) / denom
    ci_high = (centre + margin) / denom

    print(f"\n=== {label} ===")
    print(f"Config: k={num_replicas} replicas, n={EFFECTIVE_POOL_SIZE} candidates")
    print(f"Steps: {num_steps}")
    print(f"Collisions: {steps_with_collision}/{num_steps}")
    print(f"Collision rate: {collision_rate*100:.1f}%")
    print(f"95% Wilson CI: [{ci_low*100:.1f}%, {ci_high*100:.1f}%]")
    print(f"Theoretical Birthday bound (k=5, n=9): 74.4%")

    return steps_with_collision, num_steps


def run_advanced_s2():
    logger.info("=== Reproducing S2 Advanced: Constrained Pools & Shared State ===")

    # 1. Configuration
    # Use a small subnet (CIDR /28 = 16 IPs, 14 usable)
    # This forces collisions and makes the 'Randomness' less effective at isolating replicas.
    pool_cidr = "10.0.1.0/28"
    num_replicas = 5
    num_steps = 500  # Increased from 20 to 500 for the ESORICS 2026 evaluation

    logger.info(f"Configuration: Pool={pool_cidr}, Replicas={num_replicas}, Steps={num_steps}")
    logger.info("Entropy Source: SecretsProvider (CSPRNG)")

    # Condition 1: PRNG (StandardRandomProvider)
    # Purpose: show Birthday Paradox occurs regardless of entropy quality.
    logger.info("\n--- Condition 1: PRNG (StandardRandomProvider) ---")
    _run_condition(
        pool_cidr,
        num_replicas,
        num_steps,
        pool_factory=lambda _rid: VIPPool(pool_cidr, entropy_provider=StandardRandomProvider()),
        label="Condition 1: PRNG (StandardRandomProvider)",
    )

    # Condition 2: CSPRNG (SecretsProvider)
    # Purpose: confirm CSPRNG provides no structural relief.
    logger.info("\n--- Condition 2: CSPRNG (SecretsProvider) ---")
    _run_condition(
        pool_cidr,
        num_replicas,
        num_steps,
        pool_factory=lambda _rid: VIPPool(pool_cidr, entropy_provider=SecretsProvider()),
        label="Condition 2: CSPRNG (SecretsProvider)",
    )

    # Condition 3: DEAD v1.0 (entropy only, no coordination)
    # Purpose: isolate coordination as the variable — same daemon, same entropy
    # quality, but no coordination_scope or replica_id on VIPPool.
    # Birthday Paradox should persist at ~74.4%.
    server_proc = start_dead_server(DEAD_V1_PORT)
    server_url = f"http://127.0.0.1:{DEAD_V1_PORT}"
    try:
        logger.info("\n--- Condition 3: DEAD v1.0 (entropy only, no coordination) ---")
        _run_condition(
            pool_cidr,
            num_replicas,
            num_steps,
            pool_factory=lambda _rid: VIPPool(pool_cidr, entropy_provider=DeadEntropyProvider(server_url=server_url)),
            label="Condition 3: DEAD v1.0 (entropy only, no coordination)",
        )

        # Condition 4: DEAD v1.1 (entropy + epoch-key coordination)
        # Purpose: eliminate Birthday-bound collisions by partitioning a shared
        # shuffled permutation using unique replica_id slots.
        logger.info("\n--- Condition 4: DEAD v1.1 (entropy + epoch-key coordination) ---")
        _run_condition(
            pool_cidr,
            num_replicas,
            num_steps,
            pool_factory=lambda rid: VIPPool(
                pool_cidr,
                entropy_provider=DeadEntropyProvider(server_url=server_url),
                coordination_scope="pool-1",
                replica_id=str(rid),
            ),
            label="Condition 4: DEAD v1.1 (entropy + epoch-key coordination)",
        )
    finally:
        server_proc.terminate()
        server_proc.wait()

if __name__ == "__main__":
    run_advanced_s2()
