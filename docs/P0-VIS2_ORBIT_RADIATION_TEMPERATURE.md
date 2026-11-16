# P0-VIS2 — Linked orbit/radiation/temperature & world-fate viewer
Status: design specification; depends on merged VIS1 + M3; supports M4 outcomes when present.

## Product intent
One interactive time axis controlling 3D REBOUND four-body positions, individual stellar irradiation and total incoming flux, global C0 temperature, C1 latitude heat map, and real runtime/environment events. Explicitly show chaotic evolution, ejections and collisions, including the end of scientifically computed timeline.

## Viewer architecture
- Reuse VIS1 strict M2 archive reader, 3D trajectories, Plotly optional `viz` dependency and HTML-export mechanisms.
- Add independent M3 validated archive adapter. Require same `world_id` and initial-state hash, overlapping simulation interval, compatible t0/time basis and provenance. Never combine unrelated M2/M3 inputs because filenames look similar.
- Event and M4 annotation overlay may be absent. If an M4 archive is provided, verify world_id and provenance.
- Use a **single authoritative simulated time** from overlapping data interval; represent states with sampled/interpolated labels. M2 position interpolation for rendering only is not a new N-body solution. For M3 thermal values, interpolate only with an explicit diagnostic/display policy; don't reinterpret model physics.
- At or after M2 collision terminal, **freeze/stop** at final scientifically available time, display impact warning; never render nonexistent trajectories or climate extrapolation.
- Escape-candidate markers keep moving if M2 has subsequent samples. Do not hard-code "permanently escaped".
- All trace coordinates from SI meters with optional AU display; labels in W/m², K, seconds or days. Marker screen radius not physical radius. Same x/y/z scale on orbital view.

## Proposed UI
Desktop: left ~60% 3D system with 4 colored bodies, tails and events; right top stellar irradiance (3 sources + sum), right middle C0 temperature and radiation budget, right bottom C1 latitude-vs-time heatmap; full-width bottom time slider with Play/Pause, 1×/4×/16× *visual playback* and selectable event jump.
Mobile: stacked panels with shared time.
Toggle: center barycentric vs planet-relative camera (a coordinate display transform, not a different orbit). Highlight collision and escape candidates with confidence/limitation notes.
Optional world-fate panel shows M4 labels + observed time windows + source-provenance, not speculative postcollision animation.

## Implementation guidance
Start as Plotly HTML with linked traces and sliders where feasible; avoid independent Plotly subplots with unlinked frame states. If one Plotly figure's subplots make 3D + heatmap awkward, create one offline HTML with minimal JS controller that updates multiple Plotly charts from **one index/time state** and include an integration test asserting slider synchronizes all charts. Do not inject untrusted event strings into HTML; reuse escaping and serialize JSON safely.
A static line need not be included inside each animation frame; cap frames and downsample with endpoint and event preservation, announce decimation and data coverage.

## CLI
```bash
threebody viz dashboard --m2-run runs/m2 --m3-run runs/m3 --out runs/dashboard.html
# optionally --m4-run runs/m4; --frame-cap 1000; --units au|m
```
Keep `threebody viz trajectory` unchanged.

## Verification
VIS2-01: M2/M3/M4 adapters, hashes/time reference/partial status validation.
VIS2-02: linked time, four 3D locations + 3 individual and total fluxes; UI axes and units.
VIS2-03: C0 scalar temperature, C1 latitude temperature heatmap, conservative display interpolation, no silent beyond-last extrapolation.
VIS2-04: runtime/M4 events and event-aware seek; collision stop, escape candidate continuation, user-facing uncertainty descriptions.
VIS2-05: offline HTML, browser smoke test (Playwright optional), performance of 365, 3650 and 100000 samples, CI, `docs/development/VIS2_REPORT.md`.
No fake stable orbits, no fake temperatures, no precomputed explosion paths. Attractive effects may be added as explicitly **nonphysical visual effects** in a later game rendering layer.
Draft PR to main, don't auto-merge.
