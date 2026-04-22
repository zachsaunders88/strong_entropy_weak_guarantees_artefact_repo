import threading
import os
import time
import typing
from .emn import EntropyMixingNetwork
import secrets

class Reseeder:


    def __init__(
        self,
        emn: EntropyMixingNetwork,
        periodic_seconds: float | None = None,
        use_os_entropy:bool=True,
        jitter_max_seconds: float | None = None,
        jitter_frac: float | None = None,
        min_interval_seconds: float = 0.1,
        deterministic_for_tests: bool = False,
        ):
        """
        :param emn: EMN instance
        :param period_seconds: base period for periodic reseeding (wall-clock)
        :param use_os_entropy: whether to use os.urandom when no entropy provided
        :param jitter_max_seconds: maximum additional random seconds to add (uniform [0, jitter_max_seconds])
        :param jitter_frac: alternative to jitter_max_seconds; jitter_max = period_seconds * jitter_frac
        :param min_interval_seconds: don't sleep less than this (safety)
        :param deterministic_for_tests: if True, use deterministic PRNG (not CSPRNG) for jitter - useful for unit tests
        """

        # CORE ATTRIBUTES
        self.emn = emn
        self.period_seconds = periodic_seconds
        self.jitter_max_seconds = jitter_max_seconds
        self.jitter_frac = jitter_frac
        self.min_interval_seconds = min_interval_seconds
        self._stop_event = threading.Event()
        self._thread = None
        self.use_os_entropy = use_os_entropy

        # AUDITING ATTRIBUTES
        self.reseed_count = 0
        self.last_reseed_ts = None
        self.last_event = None
        self.observed_intervals = []

        if deterministic_for_tests:
            import random as _rand
            self._jitter_rng = _rand.Random(0xD0D0)
            self._secure_jitter = False
        else:
            self._jitter_rng = None
            self._secure_jitter = True

    def _sample_jitter(self) -> float:
        """ Return a non-negative jitter in seconds."""
        if self.jitter_max_seconds is None and self.jitter_frac is None:
            return 0.0
        if self.jitter_frac is not None:
            if self.period_seconds is None:
                raise ValueError("period_seconds must be set if jitter_frac is used")
            jitter_max = abs(self.period_seconds * float(self.jitter_frac))
        else:
            jitter_max = float(self.jitter_max_seconds)
        
        if jitter_max <= 0:
            return 0.0

        # Use CS RNG if requested
        if self._secure_jitter:
            micros = int(jitter_max * 1_000_000)
            if micros <= 0:
                return 0.0
            r = secrets.randbelow(micros+1) / 1_000_000
            return r
        else:
            return self._jitter_rng.random() * jitter_max

    def start_periodic(self):
        if self.period_seconds is None:
            raise ValueError("period_seconds not configured")
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._periodic_loop, daemon=True)
        self._thread.start()

    def stop_periodic(self):
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=2.0)
        self._thread = None

    def _periodic_loop(self):
        try:
            while not self._stop_event.is_set():
                loop_start = time.time()
                base = float(self.period_seconds)
                jitter = self._sample_jitter()
                sleep_time = base + jitter
                if sleep_time < self.min_interval_seconds:
                    sleep_time = self.min_interval_seconds
                
                deadline = time.monotonic() + sleep_time
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    if self._stop_event.wait(timeout=remaining):
                        return

                slept = time.time() - loop_start
                self.observed_intervals.append(slept)
                self.reseed_event("periodic")

        except Exception as e:
            print(f"[Reseeder] Fatal error in periodic loop: {e}")

    def reseed_event(self, event_name: str, entropy: bytes = None):
        """
        Trigger a reseed due to an event.
        If entropy is None and use_os_entropy==True, uses os.urandom.
        """
        if entropy is None and self.use_os_entropy:
            self.emn.reseed_from_os()
        else:
            self.emn.inject_entropy(entropy)

        # Auditing
        self.reseed_count += 1
        self.last_reseed_ts = time.time()
        self.last_event = event_name

    def close(self):
        self.stop_periodic()

        self.emn._zeroize_state()