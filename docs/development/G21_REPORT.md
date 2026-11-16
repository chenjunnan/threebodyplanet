# P0-G2.1 development report

Date: 2026-11-16  
Branch: `feature/p0-g21-bounded-initials`  
Generator/config version: `stochastic_conics/0.4.1`

## Implementation

- Added a strict 0.4.1 configuration family with independently optional planet/star distance, stellar
  pair distance, planet speed, selected-host escape proxy and combined-potential escape proxy limits.
  The 0.4.0 model and entropy layout are unchanged.
- Constraints inspect the complete four-body SI Cartesian state after all velocity kicks and final COM
  normalization. No state component is clipped or silently repaired.
- Bounded attempts use PCG64 `SeedSequence([seed,candidate_index,attempt,stream_id])`. Every attempt
  archives measured values and reason codes. Exhaustion produces `GENERATION_CONSTRAINT_EXHAUSTED` and
  batch generation continues with later candidates.
- `mode: unrestricted` accepts attempt zero without applying thresholds, retaining high-speed and distant
  chaotic worlds. `allow_initial_contact` explicitly controls bounded selection; retained contact remains
  a t=0 terminal outcome and is still rejected by the unchanged M2 guard.
- Added `configs/g21_bounded_worlds.yaml`, versioned constraint/metadata schemas, batch summary attempt
  statistics and seven physical/reproducibility/failure-policy tests.

## Proxy semantics

The absolute speed metric is the planet velocity relative to the three-star center of mass. The selected
host metric uses a two-body proxy, or a concentrated stellar-mass proxy for barycenter-host samples. The
combined metric uses `sqrt(2 G sum(m_i/d_i))` from frozen t=0 star positions. These are selection scales,
not proofs of permanent binding, stability, survival or habitability.

## Selection-bias demonstration

For 200 candidate indices with seed `20261116`, the unrestricted G2 0.4.0 sample and bounded 0.4.1 example
produced the following deterministic comparison:

| metric | G2 unrestricted | G2.1 bounded |
|---|---:|---:|
| generated / invalid | 200 / 0 | 200 / 0 |
| initial-contact rate | 0.0% | 0.0% |
| nearest-star distance above 1.5e12 m | 32.5% | 0.0% |
| combined-potential speed fraction above 0.90 | 68.0% | 0.0% |
| star-COM-relative planet speed above 80,000 m/s | 40.0% | 0.0% |
| nearest-distance median / p95 (m) | 3.292e11 / 1.014e13 | 1.941e11 / 1.107e12 |
| speed median / p95 (m/s) | 61,654 / 331,163 | 29,297 / 71,402 |
| mean / maximum attempts | 1.00 / 1 | 5.97 / 34 |

The bounded run rejected 879 attempts for the combined-potential ratio, 344 for nearest distance and 467
for absolute speed; one attempt can have multiple reasons. This demonstrates deliberate selection bias,
not improved physical truth. The configured ranges happened to yield no initial contacts in this sample;
forced-contact tests cover both policy branches.

## Verification

Executed from a clean clone with Python 3.11:

```text
pytest -q
........................................................................ [ 59%]
..................................................                       [100%]
122 passed

ruff check .
All checks passed!

python -m compileall -q src tests
completed with exit code 0
```

G2.1-specific tests cover replay/order independence, post-kick final-state metrics, every enabled bound,
unrestricted behavior, selected-host proxy equations, finite exhaustion/batch continuation, and
contact/guard separation.
