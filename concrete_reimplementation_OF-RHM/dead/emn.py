import os
import threading
import time
import hmac
import hashlib
import random
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

class EMNError (Exception):
    pass

class EntropyMixingNetwork:
    """
    Core EMN Primitive
    - Uses HKDF for mixing (extract + expand semantics).
    - Uses HMAC-SHA256(state, R) for whitening the output 0.
    - Keeps internal state in a bytearray to allow overwrite/zeroization.
    """

    STATE_LEN = 32

    def __init__(self, initial_seed: bytes = None, paper_mode_xor=False):
        """
        Initialize parameters and attributes
            :param prng: defines the CSPRNG used for main entropy source
            :param initial_seed: allow seed to be instantiated for PERSONALIZATION requirements
            :param _state: internal state variable to track seed state, derived from HMAC-based Key Derivation Function
            :param counter: internal clock for reference
        """
        self.prng = random.SystemRandom()
        self.paper_mode_xor = paper_mode_xor
        if initial_seed is None:
            initial_seed = os.urandom(self.STATE_LEN)
        if len(initial_seed) != self.STATE_LEN:
            raise EMNError("Initial seed must be 32 bytes")
        # Internal state stored as multiple bytearray for zeroization
        self._state = bytearray(self._hkdf_derive(initial_seed, salt=None, info=b"EMN-seed"))
        self.counter = 0

    def _hkdf_derive(self, ikm: bytes, salt: bytes=None, info: bytes=b"EMN"):
        """
        Main Mixing mechanism driven by HKDF; called in state transitions
            Lower-level mixer used by injection function
        """
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=self.STATE_LEN,
            salt=salt,
            info=info,
        )
        return hkdf.derive(ikm)

    def _get_prng_bytes(self, n_bytes: int) -> bytes:
        """Get n_bytes from SystemRandom (secure)"""
        bits = self.prng.getrandbits(n_bytes * 8)
        return bits.to_bytes(n_bytes, "big")

    def inject_entropy(self, entropy: bytes):
        """
        Mix externally provided entropy E into state:
            Higher level policy function for mixing
            state <- HKDF(ikm=entropy, salt=old_state, info='EMN-mix')
        """
        if not isinstance(entropy, (bytes, bytearray)) or len(entropy) == 0:
            raise EMNError("entropy must be non-empty bytes")

        # derive new state using HKDF with salt = old_state
        old_state = bytes(self._state) # bytes for salt
        new_state = self._hkdf_derive(entropy, salt=old_state, info=b"EMN-mix")
        print("EMN State Updated: ", int.from_bytes(new_state, "big"))
        #overwrite in-place
        self._zeroize_state()
        self._state = bytearray(new_state)

    def reseed_from_os(self):
        """Reseed with fresh OS entropy"""
        e = os.urandom(self.STATE_LEN)
        self.inject_entropy(e)

    def _whiten(self, state_bytes: bytes, r_bytes: bytes) -> bytes:
        """ Whitening / output function O = HMAC(state, R) using SHA256."""
        return hmac.new(state_bytes, r_bytes, hashlib.sha256).digest()

    def next(self, context: bytes = None) -> bytes:
        """
        Generate next 256-bit ouput O per algorithm:
            1. R <- PRNG.getrandbits(256)
            2. (optional) IKM = R || (context if provided)
            3. O = HMAC(state, IKM)
            4. Update state: state <- HKDF(ikm=IKM, salt=old_state, info='EMN-update')
        """
        self.counter += 1
        R = self._get_prng_bytes(self.STATE_LEN)

        # Mix Context (Personalization) if provided
        ikm = R
        if context:
            if not isinstance(context, (bytes, bytearray)):
                raise EMNError("context must be bytes")
            # We mix context into the IKM used for both output and next state
            ikm = R + context

        state_bytes = self.get_state_bytes()
        if self.paper_mode_xor:
            # Simple XOR logic doesn't support context cleanly, keeping it simple
            O = bytes(a ^ b for a, b in zip(state_bytes, R))
        else:
            O = self._whiten(state_bytes, ikm)

        new_state = self._hkdf_derive(ikm, salt=state_bytes, info=b"EMN-update")
        self._zeroize_state()
        self._state = bytearray(new_state)

        return O

    def next_int(self, **kwargs) -> int:
        return int.from_bytes(self.next(**kwargs), "big")

    def get_state_bytes(self) -> bytes:
        """Return a copy of the state"""
        return bytes(self._state)

    def _zeroize_state(self):
        """Attempt to overwrite state bytes in memory"""
        for i in range(len(self._state)):
            self._state[i] = 0
        # leave the bytearray length intact; not guaranteed to remove all copies