# src/dead/server.py

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import base64
from threading import Lock
from typing import Optional, Dict, Tuple
import time
import secrets

from .emn import EntropyMixingNetwork
from .reseeder import Reseeder

app = FastAPI(title="DEAD Entropy Service")

# Global state
emn = EntropyMixingNetwork()

def _emn_bytes(n: int, context: Optional[str] = None) -> bytes:
    """Generate n bytes from EMN by concatenating 32-byte blocks from next()."""
    chunks = []
    remaining = n
    ctx_bytes = context.encode('utf-8') if context else None
    
    while remaining > 0:
        block = emn.next(context=ctx_bytes)  # returns 32 bytes
        if len(block) == 0:
            raise RuntimeError("EMN.next() returned empty block")
        take = min(len(block), remaining)
        chunks.append(block[:take])
        remaining -= take
    return b"".join(chunks)

lock = Lock()

# Reseeder running in background
reseeder = Reseeder(
    emn,
    periodic_seconds=0.10,    # 100 ms
    jitter_frac=0.20,
    deterministic_for_tests=False
)
reseeder.start_periodic()

start_ts = time.time()


@app.get("/status")
def status():
    """Return entropy health + reseed counters."""
    return {
        "status": "ok",
        "uptime_s": time.time() - start_ts,
        "reseed_count": getattr(reseeder, "reseed_count", None),
        "last_reseed_ts": getattr(reseeder, "last_reseed_ts", None),
    }


@app.get("/entropy")
def get_entropy(n: int = 32, context: Optional[str] = None):
    """Return n bytes of entropy, base64 encoded."""
    if n <= 0:
        return JSONResponse({"error": "n must be positive"}, status_code=400)

    with lock:
        try:
            raw = _emn_bytes(n, context=context)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    encoded = base64.b64encode(raw).decode()
    return JSONResponse({"data": encoded, "n": n})


@app.get("/entropy_int")
def get_entropy_int(bits: int = 64, context: Optional[str] = None):
    """Return an integer with the requested number of random bits."""
    if bits <= 0:
        return JSONResponse({"error": "bits must be positive"}, status_code=400)

    nbytes = (bits + 7) // 8
    with lock:
        try:
            raw = _emn_bytes(nbytes, context=context)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    val = int.from_bytes(raw, "big") & ((1 << bits) - 1)
    return JSONResponse({"value": str(val), "bits": bits})

class EpochManager:
    def __init__(self, rotation_seconds: int = 60):
        self.rotation_seconds = rotation_seconds
        # scope -> (key, expires_at)
        self.scopes: Dict[str, Tuple[str, float]] = {}
        self.lock = Lock()
        
    def get_key(self, scope: str) -> str:
        with self.lock:
            now = time.time()
            if scope in self.scopes:
                key, expires = self.scopes[scope]
                if now < expires:
                    return key
            
            # Generate new key
            # Uses standard EMN/secrets for the key itself
            new_key = secrets.token_hex(16)
            expires = now + self.rotation_seconds
            self.scopes[scope] = (new_key, expires)
            return new_key

epoch_manager = EpochManager(rotation_seconds=30)

@app.get("/epoch_key")
def get_epoch_key(scope: str = "global"):
    """
    Returns a time-bounded 'Epoch Key' for coordinated permutations.
    All clients requesting the same scope within the window receive the same key.
    """
    key = epoch_manager.get_key(scope)
    return JSONResponse({"key": key, "scope": scope})
def serve_forever(host="127.0.0.1", port=8000):
    """Entry point that runs the DEAD service."""
    import uvicorn

    uvicorn.run(
        "dead.server:app",
        host=host,
        port=port,
        reload=False,
        workers=1,
    )