"""
T7 — HTTP loopback latency benchmark for the DEAD /entropy endpoint.

Starts the DEAD server as a subprocess, makes N_CALLS sequential calls to
/entropy?n=32, records per-call latency, and reports mean, p95, and p99.
"""

import os
import sys
import time
import subprocess
import statistics
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

N_CALLS   = 1000
WARMUP    = 50          # calls discarded before recording
SERVER_URL = "http://127.0.0.1:8000"
ENTROPY_ENDPOINT = f"{SERVER_URL}/entropy?n=32"
STARTUP_WAIT = 3.0      # seconds to wait for uvicorn to be ready


def wait_for_server(timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{SERVER_URL}/status", timeout=1.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def main():
    print("T7 — DEAD /entropy loopback latency benchmark")
    print(f"  Endpoint : {ENTROPY_ENDPOINT}")
    print(f"  Warmup   : {WARMUP} calls (discarded)")
    print(f"  Measured : {N_CALLS} calls")

    # Start DEAD server
    server_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn",
         "controller.dead.server:app",
         "--host", "127.0.0.1",
         "--port", "8000",
         "--log-level", "warning"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    print(f"\nWaiting for server to start ...")
    if not wait_for_server():
        server_proc.terminate()
        print("ERROR: Server did not start within timeout.")
        sys.exit(1)
    print("Server ready.")

    session = requests.Session()

    # Warmup
    for _ in range(WARMUP):
        session.get(ENTROPY_ENDPOINT)

    # Measured calls
    latencies_ms = []
    for i in range(N_CALLS):
        t0 = time.perf_counter()
        r = session.get(ENTROPY_ENDPOINT)
        t1 = time.perf_counter()
        if r.status_code != 200:
            print(f"  WARNING: call {i} returned HTTP {r.status_code}")
        latencies_ms.append((t1 - t0) * 1000.0)

    server_proc.terminate()

    # Statistics
    latencies_ms.sort()
    mean_ms   = statistics.mean(latencies_ms)
    median_ms = statistics.median(latencies_ms)
    p95_ms    = latencies_ms[int(0.95 * N_CALLS)]
    p99_ms    = latencies_ms[int(0.99 * N_CALLS)]
    min_ms    = latencies_ms[0]
    max_ms    = latencies_ms[-1]

    print(f"\n=== DEAD /entropy Loopback Latency ({N_CALLS} calls) ===")
    print(f"  Mean   : {mean_ms:.3f} ms")
    print(f"  Median : {median_ms:.3f} ms")
    print(f"  p95    : {p95_ms:.3f} ms")
    print(f"  p99    : {p99_ms:.3f} ms")
    print(f"  Min    : {min_ms:.3f} ms")
    print(f"  Max    : {max_ms:.3f} ms")

    if p99_ms < 10.0:
        print(f"\n  VERDICT: p99 {p99_ms:.3f} ms — operationally negligible at all "
              f"evaluated mutation rates.")
    else:
        print(f"\n  NOTE: p99 {p99_ms:.3f} ms — warrants throughput discussion at "
              f"high mutation rates.")


if __name__ == "__main__":
    main()
