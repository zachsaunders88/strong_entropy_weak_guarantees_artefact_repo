# Network-level validation (Mininet/OVS/os-ken) against logical findings

This table is the full per-mode comparison referenced by the paper's network-level
validation discussion (Section 5.4, "Network-Level Validation", and Appendix A). It is
held here rather than inline in the paper for length reasons. Each logical-layer result is
reproduced at the network layer using Mininet, Open vSwitch, and os-ken; the rightmost
column records whether the network observation is consistent with the logical finding.

| Mode | Logical result | Network observation | Consistent? |
|------|----------------|---------------------|-------------|
| S1  | 100% PRNG; 0% else            | 100% PRNG (10 trials, CI [72.2%, 100%]); ~6% else            | Yes |
| S2  | ~74.4% (k=5)                  | k=5: 72.8–75.9%; k=3: 30.3–33.0% (theory 30.9%)             | Yes (k=3 extends coverage) |
| S3  | σ ~1.3 ms / 130 ms (DEAD)     | σ ~14 ms (KS p=0.28); 217 ms DEAD (D=1.0)                   | Yes |
| S4c | 0 ms / 362 ms (DEAD)          | 0 ms (50/50); 587–619 ms mean (DEAD)                       | Yes |
| S4d | 11.0%; 1/9 theory             | 14.0% (7/50, Wilson CI [7.0%, 26.2%]); silent OVS overwrite | Yes (more severe) |
| S5  | silent at address layer; fail-loud halts | KS p_addr=0.872; p_time < 1e-200; 885 → 9 mutations | Yes (timing side-channel) |

Notes:

- **S4d** is more severe at the network layer than in the logical model: colliding
  `add-flow` rules with different rewrite actions are treated by OVS as a silent upsert,
  redirecting traffic to the wrong real IP rather than failing visibly.
- **S5** is invisible at the address layer (KS p=0.872) but detectable through
  inter-mutation timing (the silent fallback incurs a ~1 s HTTP timeout per failed fetch);
  the fail-loud configuration instead collapses mutation activity (885 → 9).

## Provenance

Raw result files for the rows reproducible from this export live alongside this document
in `results/`; the driver scripts are in `experiments/`.

| Mode | Driver | Raw results |
|------|--------|-------------|
| S1  | `experiments/s1_network_prediction.py` | `results/s1_comparison_*.json`, `results/s1_network_prediction_*.json` |
| S2  | `experiments/s2_network_collision.py`  | `results/s2_network_collision_*.json` |
| S3  | `experiments/s3_network_timing.py`     | `results/s3_comparison_*.json`, `results/s3_network_timing_*.json` |
| S4c | `experiments/s4c_network_timing.py`    | `results/s4c_comparison_*.json`, `results/s4c_network_timing_*.json` |
| S4d | full concrete reimplementation (constrained-pool clone); not included in this export | — |
| S5  | full concrete reimplementation (silent-degradation); not included in this export | — |

The S4d and S5 network experiments were run in the full Mininet/OVS concrete
reimplementation and are not part of this trimmed evaluation export; their logical-layer
counterparts are reproducible via the logical reimplementation
(`logical_reimplementation_OF-RHM/`).
