import time
import threading
import logging
from typing import List, Callable
from shared.vip_allocator import HostRecord, VIPPool
from shared.logger import setup_logger

logger = setup_logger("mutation_scheduler")

class MutationScheduler:
    def __init__(self, vip_pool: VIPPool, hosts: List[HostRecord],
                 use_jitter: bool = True):
        self.vip_pool = vip_pool
        self.hosts = hosts
        self.use_jitter = use_jitter
        self.running = False
        self.thread = None

    def start(self):
        self.running = True

        # S4c fix: invalidate inherited snapshot deadlines.
        # Without this, two clones restored from the same snapshot
        # share an identical host.history[-1][1] timestamp and fire
        # their first post-resume mutation at the exact same moment.
        # Drawing an independent random offset per host per clone
        # ensures divergence immediately after resume.
        now = time.time()
        for host in self.hosts:
            if host.history:
                try:
                    # get_jitter(interval, 1.0) returns a value in
                    # [interval, 2*interval]; subtracting interval
                    # gives an offset in [0, mutation_interval_s],
                    # covering the full mutation period.
                    offset = self.vip_pool.entropy_provider.get_jitter(
                        host.mutation_interval_s, jitter_fraction=1.0
                    ) - host.mutation_interval_s
                except Exception:
                    # Fallback: if entropy provider fails, use stdlib
                    # random. Safe here because the offset only affects
                    # timing, not address-selection security.
                    import random as _random
                    offset = _random.random() * host.mutation_interval_s

                # Back-date last mutation timestamp by offset.
                # _loop() computes: elapsed = now - last_mutation_ts
                # Independent offsets → independent elapsed values
                # → first mutations fire at different times.
                last_vip, _ = host.history[-1]
                host.history[-1] = (last_vip, now - offset)

        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        logger.info("Mutation scheduler started")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()
        logger.info("Mutation scheduler stopped")

    def _loop(self):
        # In a real system, we might use a priority queue or individual timers.
        # For this logical sim, a simple loop checking all hosts is fine.
        while self.running:
            now = time.time()
            for host in self.hosts:
                # Check if it's time to mutate
                # We need to track last mutation time. 
                # HostRecord has history, we can check the last entry.
                last_mutation = 0.0
                if host.history:
                    _, last_mutation = host.history[-1]
                
                if now - last_mutation >= host.mutation_interval_s:
                    self.mutate_host(host)
            
            # S3 countermeasure: jittered sleep makes mutation timing unpredictable.
            # When use_jitter=False, sleep is fixed — reproducing the S3 vulnerable baseline.
            if self.use_jitter:
                sleep_time = self.vip_pool.entropy_provider.get_jitter(1.0, jitter_fraction=0.5)
            else:
                sleep_time = 1.0  # fixed interval — S3 vulnerable baseline
            time.sleep(sleep_time)

    def mutate_host(self, host: HostRecord):
        try:
            old_vip = host.current_vip
            new_vip = self.vip_pool.choose_new_vip(host)
            logger.info(f"Mutated host {host.name}: {old_vip} -> {new_vip}")
        except Exception as e:
            logger.error(f"Failed to mutate host {host.name}: {e}")
