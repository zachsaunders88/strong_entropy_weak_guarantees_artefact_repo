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

## Reproducing Table 4 S4c row (clone deadline divergence)

```bash
python eval/s4c_deadline.py
```

## Reproducing Table 5 (NIST SP 800-22 entropy quality)

```bash
python nist/run_nist.py
```

## DEAD daemon

```bash
python dead/daemon.py [--port PORT]
```

## Notes

Linux is recommended for full `/dev/urandom` kernel entropy pool behaviour. Mininet/OVS network-level validation requires Linux with Mininet and Open vSwitch installed.
