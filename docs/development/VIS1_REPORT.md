# P0-VIS1 development report

Date: 2026-11-16  
Branch: `feature/p0-vis1-trajectory-viewer`  
Design baseline: `design/p0-vis1-trajectory-viewer` at `b8186ca`  
Main baseline: M2 merge `589f11d`

## Delivered milestones

### VIS1-01 — M2 archive reader and validation

- Reused the exact M2 `TrajectorySample`, `SimulationEvent`, and `IntegrationResult` Pydantic models.
- Added bounded JSONL reading with strict rejection of missing files, blank lines, invalid UTF-8/JSON,
  missing fields, non-finite values, noncanonical body IDs/order, non-increasing time, inconsistent
  manifest sample/event counts, and mismatched start/final times. The explicit endpoint tolerance is
  `1e-9 s` with zero relative tolerance.
- `COMPLETED` and `COLLISION` display normally. `STOPPED` and `FAILED` display only existing complete
  samples and are marked partial; a failed archive with no complete sample is rejected.
- Input is capped by `max_input_samples` with an absolute implementation maximum of 1,000,000 samples.
  VIS1 does not follow the optional manifest `initial_source` path.

### VIS1-02 — 3D trajectories and display transforms

- Added explicit meter/AU conversion using `1 AU = 149597870700 m`; no stored state is altered.
- Added four static 3D paths, four current-position symbols, fixed colors/labels, legend controls, and
  `scene.aspectmode='data'` to prevent axis distortion.
- Page annotations identify `BARYCENTRIC_INERTIAL`, display units, relative epoch, nonphysical marker
  sizes, and the distinction between animation duration and simulation time.
- Single-sample and zero-event archives render without animation controls.

### VIS1-03 — animation and events

- Added archived-time slider plus play/pause controls. Frames contain only four dynamic point traces at
  indices `[5, 6, 7, 8]`; the four full paths and event trace are not duplicated per frame.
- Frame reduction is distributed across the full interval and always retains first/last plus both
  bracketing samples for every event. A frame cap too small to retain those samples is rejected.
- Event positions use piecewise-linear interpolation for exactly the IDs in `event.body_ids`: midpoint
  for multi-body events and the declared body position for one-body escape candidates. No nearest-pair
  inference or extrapolation is performed.
- Event messages and detection methods are HTML-escaped before Plotly serialization. Tooltips state that
  event coordinates are sampling interpolation estimates, not exact physical contact positions.

### VIS1-04 — CLI, optional dependency, documentation, and CI

- Added `threebody viz trajectory --run ... --out ...` with `--frame-cap`, `--max-input-samples`,
  `--units au|m`, barycentric center selection, `--cdn`, and explicit `--overwrite`.
- Added optional `viz=["plotly>=5.18,<7"]`; importing the ordinary CLI does not import Plotly. A missing
  dependency produces an installation instruction instead of breaking M0/M1/M2 commands.
- Offline HTML embeds Plotly JS by default and is written through a sibling temporary file plus atomic
  replacement. CDN mode is explicit and produces a smaller network-dependent document.
- CI installs `.[dev,physics,viz]` and runs the unchanged Python 3.11/3.12 test matrix.

## Verification

Local environment: Python 3.11.18, NumPy 2.4.6, SciPy 1.17.1, Pydantic 2.14.3,
REBOUND 4.6.0, Plotly 6.9.0, Ruff 0.17.5. The final run includes the M3 code now merged
into `main`; VIS1 remains a separate package and has no M3 imports.

```text
pytest -q
103 passed in 3.41s

ruff check .
All checks passed!

python -m compileall -q src tests
completed with exit code 0
```

The full suite contains 103 tests; 22 VIS1 tests cover archive corruption/limits, unit conversion,
event interpolation and ID
attribution, distributed frame selection, trace/frame structure, single/partial archives, script
injection, offline/CDN resources, overwrite policy, and a real IAS15-to-HTML CLI path.

## Real M2 demonstration

An accepted M1 initial state was integrated by REBOUND IAS15 for 864,000 seconds with output every
43,200 seconds. The resulting M2 archive was `COMPLETED` with 21 samples and zero runtime events.

```text
threebody viz trajectory \
  --run runs/vis1-demo/m2 \
  --out runs/vis1-demo/trajectory.html \
  --frame-cap 21
```

Actual export result:

```text
input_samples=21
displayed_frames=21
top_level_traces=9
events=0
plotly_js=embedded
file_size_bytes=4907600
frame_updates_with_dynamic_trace_indices_[5,6,7,8]=21
external_plotly_script=false
```

Automated event fixtures separately verify interpolated collision/encounter and one-body semantics,
including malicious message escaping. The real demonstration intentionally reports zero events rather
than inventing one.

## Limits and follow-up

- VIS1 is diagnostic visualization, not a stability test, collision solver, or scientific validation
  replacement. It is independent of M3 and does not visualize thermal fields.
- Event coordinates are linear sampling estimates because M2 events contain no position. They can be
  inaccurate across sparse output or close encounters even when the M2 event time is valid.
- Static paths retain every accepted input sample, while animation frames may be reduced with explicit
  metadata. Very large archives should use a future streaming/binary VIS2 design.
- Visual symbol sizes are pixels, not physical radii. Stellar disks, occlusion, camera-scale radius,
  velocity arrows, and planet-centered coordinates are not implemented in VIS1.
- A self-contained Plotly document is intentionally several megabytes. `--cdn` reduces size but is not
  offline-capable.
