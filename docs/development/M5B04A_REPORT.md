# M5B-04a — Verified M5A orbit/time/event Web observer

Status: Draft delivery; based on main containing merged PR #13. Does **not** claim complete M5B-04 science dashboard.

## Implemented
- `GET /api/v1/runs/{run_id}/trajectory?start_s=&end_s=&max_points=`: reads immutable committed M5A trajectory through `SegmentedTrajectoryReader`; verifies segment manifest and artifact hashes; returns **only actual** physical simulation samples with fixed 4-body IDs and SI position/time units. Uses two sequential passes for bounded memory, deterministic uniform index reduction, preserves first/last *within query window*, limits display to 2–2000 samples.
- `GET /api/v1/runs/{run_id}/events?start_s=&end_s=`: reads confirmed archived M5A event records (up to 10,000/request). `ESCAPE_CANDIDATE` remains a candidate; no permanent escape inference.
- Strict query parameter allowlist, finite clock values, reject duplicate parameters, excessive display budget and invalid time range. Existing path guard rejects symlinks/parent traversal. No M5A write.
- Web viewer: 3D rotating **projection** on a canvas (not a photorealistic planetary renderer), 4 celestial bodies, trails only up to selected recorded time, range selector, SI time scrubber and play/pause for display only, event jump with explicitly reported difference to nearest sampled timestamp. Existing M5B-03 physical task controls stay independent of viewing playback.
- Versioned OpenAPI definitions and tests using a **real** two/three-segment IAS15 archive, HTTP requests, range boundaries and corrupted-segment rejection.

## Important science distinction
This first increment uses only the M5A Newtonian trajectory archive. It does **not** silently attach an unrelated M3 archive; M3/C0/C1 and M4 source identity and hash compatibility are still enforced by their existing reader contract and require an explicit segmented-M5A-to-thermal/outcome adapter plus linked range API. Browser event jump selects the nearest **real sample** instead of interpolating arbitrary four-body phase space, with timestamp offset displayed.

## Remaining M5B-04b acceptance (blocking full M5B-04/P0)
1. Implement M5A committed-segment → M3 incrementally computed flux/C0/C1 archive with unambiguous world_id and source hashes; separate physical-computation times from UI times.
2. M4 classification and event timeline adaptation to M5A/M3 joint provenance; no post-collision continuation.
3. Use a single, source-checked API time base for trajectory/flux/C0/C1/M4, with explicit missing values and source-data gaps; maintain C0-only/C1-only behavior.
4. Load only bounded viewport ranges in browser and provide synchronized flux and temperature panels, event jumps and browser E2E QA on real long-run results.
5. Profile memory, CPU and test thousands of immutable segments, plus security cases for active/partial archive and integrity failure.

## Validation
CI must execute `pytest -q`, `ruff check .`, `python -m compileall -q src tests` on Python 3.11 and 3.12. This file is a design/implementation record, not proof that CI already succeeded.

## Limits
The API verifies the full M5A archive on each request (O(total archive size) integrity scan) and then makes two streaming passes over the selected range. This is memory bounded but can be slow for huge archives. A validated per-segment hash/index cache and bounded range locator are future optimizations. The Canvas uses a simple geometrical projection and displays observed sample trails, not a solver or surface/lighting renderer.
