# Artifact mapping (paper claim → reproduction entrypoint)

## Logical reimplementation

Run from `logical_reimplementation_OF-RHM/`:

- Unit/integration suite: `make test`
- S2 (advanced): `python3 tests/reproduce_s2_advanced.py`
- Attacker metrics: `python3 tests/attacker_metrics.py`
- DEAD latency benchmark: `python3 tests/benchmark_dead_latency.py`
- NIST battery runner: `python3 tests/run_nist_battery.py`

## Concrete reimplementation

Run from `concrete_reimplementation_OF-RHM/`:

- Environment check: `make verify-env`
- S1: `make run-s1`
- S2: `make run-s2`
- S3 (Mininet timing): `make run-s3-all`
- S4c: `make run-s4c`
- Network-validation summary (all modes vs. logical findings): `results/network_validation_comparison.md`

Notes:
- Concrete experiments require Linux + Mininet/OVS (some targets require `sudo`).
- This export intentionally excludes paper sources and most generated artifacts (logs, DBs, NIST `.bin` files) by default.
