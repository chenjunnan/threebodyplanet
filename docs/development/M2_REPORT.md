# P0-M2 development report

Date: 2026-11-16  
Branch: `feature/p0-m2-nbody`  
Baseline: `main` at `7adb6f703aea0582ab77f84e501975d41de27920`

## Delivered milestones

### M2-01 — contracts and mandatory guard

- Added strict `0.2.0` simulation settings, trajectory/event/diagnostic/result models.
- Added safe YAML loading, canonical settings SHA-256, JSON Schema and example IAS15 config.
- Added a solver input guard independent from S1. It rejects invalid solver-domain inputs even when
  M1 used `raw` screening.
- Preserved the M1 `WorldInitialState` schema and `world generate` behavior.

### M2-02 — REBOUND IAS15

- Added a REBOUND 4.x adapter with explicit SI `G`, IAS15 epsilon, four massive particles, exact output
  targets and a true final sample for non-divisible intervals.
- Initial Cartesian data is not shifted or reconstructed. Softening is fixed off in P0-M2.
- Added bounded in-memory facade for tests and callback-based streaming for production archives.

### M2-03 — numerical diagnostics

- Added kinetic, pair-potential and total energy; linear/angular momentum; barycenter position/velocity.
- Added scaled and conditionally valid traditional energy errors, angular-momentum error and barycenter
  drift summaries.
- Collision-terminated results explicitly disable post-contact conservation interpretation.

### M2-04 — runtime events

- Collision: REBOUND swept-line detection with causal participants captured from the custom resolver
  callback, including collisions between output samples. Reported timing is limited by the solver's
  internal collision step.
- Close encounter: threshold by absolute distance or radius multiple, first observed crossing and pair
  minimum observed distances; no force modification and no continuous-minimum claim.
- Escape candidate: remaining-system COM distance, outward radial velocity, two-body specific energy and
  persistence. This is a heuristic candidate, not a permanent-escape proof.

### M2-05 — archive and CLI

- Added `threebody world simulate --initial ... --config ... --out-dir ...`.
- Added streaming `trajectory.jsonl`, events, diagnostics, resolved settings and versioned manifest.
- Archives are built in a sibling temporary directory and atomically renamed. Solver failures are stored
  as `FAILED`, never `COMPLETED`; collisions are expected successful terminal events.

### M2-06 — verification and documentation

- Added mandatory-input, physical benchmark, full-mass, conservation, event, archive, CLI and M1
  regression coverage.
- Updated README, project version and architecture decisions.

## PR #1 review corrections

- Collision attribution now comes directly from a retained custom REBOUND `collision_resolve`
  callback. The callback records `p1/p2`, requests `simulation.stop()`, returns without changing any
  particle, and the event preserves both callback indices and canonical body IDs. Regression coverage
  proves a closer terminal pair cannot replace the causal callback pair.
- A real IAS15 integration and archive test uses a deterministic sub-nanosecond wall limit. It verifies
  `STOPPED`, CLI exit code 1, a partial valid trajectory, and exact agreement between manifest
  `t_final_s` and the final trajectory sample.
- The mandatory guard now evaluates representable pair separation/acceleration and complete initial
  mechanical diagnostics. Extreme finite masses, velocities, and coordinates outside float64 mechanics
  are rejected with an input-domain explanation. Runtime observer/diagnostic non-finite values request a
  safe stop and archive as `FAILED` rather than emitting invalid JSON.
- Close-encounter output now uses `minimum_observed_pair_distances_m` and carries
  `internal_step_endpoint_observation` semantics. It is explicitly not a continuous-orbit minimum.
- GitHub Actions runs mandatory physics tests and Ruff on Python 3.11 and 3.12, explicitly imports
  REBOUND, and does not permit dependency-based test skipping.

## Scientific limits

- IAS15 computes real Newtonian point-mass gravity with finite radii used for event detection. It does
  not model tides, relativity, stellar evolution, fragmentation or collision outcomes.
- Swept-line collision detection and heartbeat observations have finite internal-step precision.
- A close-encounter threshold crossing can occur between observer calls; recorded crossing time is an
  observed approximation. Minimum distance is the minimum observer-sampled value.
- `ESCAPE_CANDIDATE` is deliberately conservative terminology. Chaotic four-body evolution may later
  recapture a body.
- M2 does not establish long-term stability or habitability and does not include climate or rendering.

## M3/M2 follow-up preparation

- Binary trajectory formats or chunked compression can replace JSONL without changing the integrator.
- Collision outcome models should become a separate non-conservative phase with new diagnostics.
- Long pre-screen runs need resumable REBOUND SimulationArchive checkpoints and resource quotas.
- Event-time refinement can bracket and reintegrate close approaches when higher timing precision is
  scientifically required.

## Verification record

Commands executed on the development branch:

```text
pytest -q
62 passed

ruff check .
All checks passed!

python -m compileall -q src tests
completed with exit code 0
```

An end-to-end CLI audit generated one accepted M1 candidate and integrated it for 31,557,600 seconds
with REBOUND 4.6.0 IAS15. The result was `COMPLETED` with 367 strictly increasing trajectory
samples and one observed event. Maximum scaled energy error was `4.4380280277216783e-16`; maximum
angular-momentum relative error was `3.9420480910091846e-16`. All archive JSON/JSONL parsed and none
contained `NaN` or `Infinity`.

A dedicated Conda environment using Python 3.11.18 was subsequently created and isolated from the
host ROS Python path. In that supported environment, all 62 tests, Ruff 0.17.5, `pip check`, and
`compileall` passed with NumPy 2.4.6, Pydantic 2.14.3, PyYAML 6.0.3, and REBOUND 4.6.0.
