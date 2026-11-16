# P0-M4 development report

Date: 2026-11-16  
Branch: `feature/p0-m4-world-outcomes`  
M4 schema version: `0.4.0`

## Implemented scope

- M4-01 validates initial state, M2 trajectory/events/manifest, M3 flux/C0/C1/energy/manifest and optional
  G2 initial-contact artifacts. World ID, initial hash, M2 manifest hash, time coverage, counts, monotonic
  sampling, C1 vector shape and SI contracts are checked before combining data.
- M4-02 computes sampled extrema, linearly crossed cold/hot exposure durations, longest episodes,
  temperature rate, C1 cold/hot band fractions, irradiance variability and trapezoidal incoming energy.
  Derived events carry interval uncertainty and detection methods.
- M4-03 separates scientific input status, dynamical outcome and environmental labels. Star-star and
  planet collisions remain distinct; escape candidates are nonterminal; initial contact works without
  M2/M3; failed or missing thermal data is explicit.
- M4-04 adds preference-only scenario scoring and the atomic `threebody world assess` archive. It never
  rejects or hides the raw report.
- M4-05 adds unit, event-semantic, initial-contact, provenance-failure and real M2+M3 end-to-end tests.

## Outputs

`world_outcome_manifest.json`, `environment_events.json`, `environment_statistics.json`,
`world_classification.json`, `world_timeline.jsonl`, `world_assessment_summary.json` and
`resolved_m4_config.json` are written through a temporary directory and atomically committed.

## Scientific limits

Thresholds are configurable diagnostic values, not habitability or local-weather criteria. C1 is
longitude averaged. Linear interpolation is used only to estimate threshold duration inside archived
sampling intervals. Collision aftermath and post-terminal temperatures are unavailable. A completed M2
window does not prove orbital stability, and an escape candidate does not prove permanent ejection.

## Verification

Executed with Python 3.11:

```text
pytest -q
........................................................................ [ 59%]
.................................................                        [100%]
121 passed

ruff check .
All checks passed!

python -m compileall -q src tests
completed with exit code 0
```

The end-to-end fixture ran a real 1,000 s REBOUND IAS15 archive, consumed it through M3 radiation/C0/C1,
then produced and provenance-validated the complete M4 atomic archive.
