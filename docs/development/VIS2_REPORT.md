# P0-VIS2 development report

Date: 2026-11-16
Branch: `feature/p0-vis2-linked-dashboard`

## Implemented scope

- VIS2-01: strict M2/M3 adapter validates world ID, initial-state hash, M2 manifest hash, status, counts,
  monotonic times, overlap, C0/C1 alignment and C1 band shapes. Optional M4 verifies both source hashes.
- VIS2-02: one authoritative time drives four 3D bodies, three independent stellar fluxes and total flux.
  AU/m units and equal 3D aspect are explicit; barycentric and planet-relative cameras are display-only.
- VIS2-03: C0 scalar temperature and C1 latitude heatmap share the controller. Linear interpolation is
  labeled display-only and prohibited outside the strict M2/M3 overlap.
- VIS2-04: M2 and optional M4 events support seek. Collision stops at the archive end; escape candidates
  remain nonterminal when later data exists. User-facing text preserves uncertainty and method fields.
- VIS2-05: offline embedded-Plotly HTML, responsive layout, safe JSON/text handling, bounded frame
  selection for 365/3650/100000 samples, Node controller smoke test and real Chromium interaction test.
  VIS1 remains unchanged.

## Interaction and safety verification

The automated Node harness executes the generated controller with stubbed Plotly/DOM APIs and asserts
that one slider update reaches orbit, flux, C0 and C1. A real Playwright Chromium 141 headless session
loaded the fully offline HTML and exercised slider movement, Play/Pause, planet-relative camera and event
jump. It observed four Plotly charts, no page errors and an explicit no-extrapolation status. Malicious
`</script><script>` and markup event strings remained inert text; serialized `<`, `>` and `&` are escaped.

## Performance and data limits

Input is capped at one million samples and defaults to 100,000. Display frames are capped at 10,000 and
default to 1,000 while retaining overlap endpoints and event times. Tests cover selection from 365, 3,650
and 100,000 samples. Static line/heat-map data is stored once; frame updates do not duplicate full source
archives. Very large C1 archives should use a smaller frame cap for browser memory.

## Real archive demonstration

The repository's existing one-year IAS15 archive for `candidate-000000` was consumed without modifying
or recomputing its 367 trajectory samples. A fresh M3 archive was then produced from the checked-in
`configs/m3_thermal.yaml` with real RK45 C0 and BDF C1 integrations, followed by:

```text
threebody viz dashboard \
  --m2-run runs/sample/simulations/000000 \
  --m3-run /tmp/threebody-vis2-real-m3 \
  --out /tmp/threebody-vis2-real-dashboard.html \
  --frame-cap 365
```

Observed summary: world `candidate-000000`; M2 `COMPLETED`; M2/M3 samples 367/367 over
0–31,557,600 s; 365 displayed frames; one archived M2 event; no M4 input; embedded Plotly HTML size
5,295,132 bytes. M3's energy diagnostics remained available unchanged: C0 normalized residual
`1.945260330229052e-15`, C1 normalized residual `1.1863049898709789e-15`, and zero reported C1 global
transport imbalance. These values validate the source archive, not the renderer itself. Generated
scientific archives and the 5.3 MB HTML are intentionally not committed.

Failure-path tests reject a mismatched M2 manifest hash, non-overlapping M2/M3 time ranges, malformed
or non-monotonic series, inconsistent C1 band shapes, over-limit inputs, and optional M4 data whose M2
or M3 provenance hashes do not match. The exporter also refuses overwrite unless explicitly requested.

## Scientific limits

The viewer is read-only. Linear display interpolation is neither IAS15 reintegration nor thermal
re-solving. C1 remains longitude averaged; marker size is not physical radius; visual playback speed is
not physical time speed. No trajectory or temperature is invented after collision or beyond a missing
thermal interval. M4 labels, when supplied, remain diagnostic classifications rather than game-physics
proofs.

## Verification

Executed from the repository root with the Python 3.11 Conda environment:

```text
pytest -q
........................................................................ [ 57%]
......................................................                   [100%]
126 tests collected; exit code 0

ruff check .
All checks passed!

python -m compileall -q src tests
exit code 0
```

The browser-only smoke used Playwright 1.56.1 with Chromium 141 outside the project dependency set. It
is a development verification, not a new runtime dependency.

## Review fix — 2026-11-17

The original frame selector returned `source samples ∪ mandatory times` directly whenever the source
sample count itself fit the cap. An event between source samples therefore added a new display time after
the capacity decision and could make the result larger than `frame_cap`. Selection now reserves capacity
for overlap endpoints and every in-range event first, then uniformly samples source times using only the
remaining budget. It raises a clear error when mandatory times alone cannot fit. Regressions cover
multiple off-sample event times at the exact cap and the mandatory-overflow failure path.
