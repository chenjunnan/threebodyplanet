# P0-G2 development report

Date: 2026-11-16  
Branch: `feature/p0-g2-stochastic-worlds`  
Generator/config version: `stochastic_conics/0.4.0`

## Implemented scope

- G2-01: added a strict discriminated 0.4.0 config family for stellar/planetary physical distributions,
  three conic distributions, host weights, orientation, anomaly and velocity kicks. Legacy 0.1.0 models
  are retained without added fields or defaults. Added dedicated JSON schemas and the broad exploration
  example `configs/g2_chaotic_worlds.yaml`.
- G2-02: implemented real two-body conic-to-Cartesian equations in SI, valid elliptic/hyperbolic anomaly
  domains, isotropic 3D orientation, weighted host selection, stellar-system planet recoil, bounded
  isotropic velocity kicks and final four-body COM normalization. The recoil translation preserves all
  three constructed relative states simultaneously. Exact parabolic samples are rejected
  with `PARABOLIC_UNSUPPORTED`.
- G2-03: S0 and configurable S1 semantics are unchanged. Initial spherical contact is preserved as a
  versioned terminal artifact with `integration_performed=false`; the unchanged M2 guard rejects the
  same state before REBOUND.
- G2-04: batch generation writes per-candidate replay metadata and terminal-contact artifacts. G2 summary
  statistics include selected hosts, conic types, eccentricity, instantaneous distance, post-kick speed,
  invalid reason codes and initial-terminal counts.
- G2-05: added reproducibility, order independence, legacy golden, strict parsing, distribution bounds,
  isotropy, elliptic/hyperbolic energy, angular momentum, barycenter, contact/guard and archive tests.

## Scientific and numerical semantics

The generator creates initial states, not stable orbits. It deliberately accepts non-hierarchical,
hyperbolic, soon-colliding and escaping scenarios. No generated state is labeled surviving, habitable or
long-term stable. Uniform true anomaly is explicitly used and must not be interpreted as uniform time or
mean anomaly. A recorded velocity kick invalidates any claim that the pre-kick conic elements remain the
osculating elements of the final state.

The generator uses NumPy PCG64 with fixed independent `SeedSequence([seed,candidate_index,stream_id])`
substreams. Stored and calculated core quantities are SI float64 and are rejected on non-finite or
unrepresentable results. M2 remains the authority for runtime collisions, close encounters, escape
candidates and conservation diagnostics.

## Verification

Executed in Conda environment `threebody-py311` (Python 3.11):

```text
pytest -q
........................................................................ [ 63%]
...........................................                              [100%]
115 passed

ruff check .
All checks passed!
```

The G2-specific suite contributes 12 tests. Legacy golden checks assert the existing example resolved
config SHA-256 and byte-identical canonical JSON against a frozen 0.1.0 reference algorithm for
candidates 0, 1 and 2 under the active NumPy runtime. The complete suite covers M0/M1, M2, M3 and VIS1
regressions.

A real CLI demonstration generated 20 stochastic candidates; all 20 produced finite initial states and
the summary contained all host/conic/distance/speed fields. Candidate 0 was then evolved with the existing
`configs/m2_ias15.yaml` REBOUND IAS15 configuration for 31,557,600 s. It completed with 367 samples,
no events, and maximum scaled energy error `3.007915322266012e-16`. This is a compatibility/numerical
check for that run, not evidence that arbitrary G2 worlds are stable.

## Deferred work

M4 environment-outcome classification and VIS2 coupled orbit/radiation/temperature observation remain
unimplemented until G2 technical review. A future review should decide whether a separately versioned
parabolic construction is needed and whether the optional unconstrained correlated Cartesian mode merits
a new generator version; neither is silently approximated in 0.4.0.
