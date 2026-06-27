# Strong Entropy, Weak Guarantees

Evaluation code for: Strong Entropy, Weak Guarantees: Governance Failures in
Distributed Moving Target Defence. ESORICS 2026.

## Requirements

Python 3.13.3

```bash
pip install -r requirements.txt
```

## Reproducing Table 2 (S2 cross-replica collision)

```bash
python eval/s2_collision.py
```

## Reproducing Table 3 S4c row (clone deadline divergence)

```bash
python eval/s4c_deadline.py
```

## Reproducing the EMN statistical-quality results (Section 5.3 / Appendix A)

```bash
python nist/run_nist.py
```

## Network-level validation (Mininet/OVS/os-ken)

The full per-mode comparison of logical findings against the network layer is in
[`concrete_reimplementation_OF-RHM/results/network_validation_comparison.md`](concrete_reimplementation_OF-RHM/results/network_validation_comparison.md).
Driver scripts are under `concrete_reimplementation_OF-RHM/experiments/` and raw results
under `concrete_reimplementation_OF-RHM/results/`.

## DEAD daemon

```bash
python dead/daemon.py [--port PORT]
```

## Notes

Linux is recommended for full `/dev/urandom` kernel entropy pool behaviour. Mininet/OVS network-level validation requires Linux with Mininet and Open vSwitch installed.
