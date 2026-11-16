# P0-M0/M1 implementation decisions

## 0.1.0 — 2026-11-16

- The resolved configuration SHA-256 covers the complete resolved experiment configuration. It
  excludes CLI output paths and generation timestamps because neither is part of the configuration.
- Candidate random streams use `SeedSequence([seed, candidate_index, stream_id])` with PCG64.
  Screening never consumes generator randomness; changing screening settings changes the audit hash
  but not the generated body parameters.
- `hierarchical_orbit` implements circular two-body approximations for an inner stellar pair, the
  outer third star, and a planet around a configured host. The whole four-body state is then shifted
  into the barycentric inertial frame. This is an initial-state construction, not a stability claim.
- Model parsing is strict. YAML numeric fields must be numbers rather than numeric strings, unknown
  fields are rejected, and structured JSON is emitted with non-finite values forbidden.
- P0's M2 boundary is represented only by typing protocols. No integration or runtime-event behavior
  is included.
- Screening reports include explicit initial-state diagnostics (signed total mass, barycenter position
  and velocity, minimum surface gap, and maximum relative speed). These are descriptive initial-state
  values only and make no long-term stability claim.

## 0.2.0 — 2026-11-16

- M2 is an explicitly authorized phase extension governed by `docs/P0-M2_IMPLEMENTATION_SPEC.md`;
  M0/M1 state schema `0.1.0`, generation CLI behavior, and resolved-config hash remain compatible.
- The numerical backend is REBOUND 4.x IAS15 with the SI value of `G` set explicitly. Initial Cartesian
  data is passed through unchanged: no implicit `move_to_com`, orbital-element reconstruction, or
  gravitational softening is applied.
- Solver-domain validation is mandatory and independent of configurable S1 screening. It rejects
  non-positive or non-finite masses, negative/non-finite radii, coordinate singularities, initial
  contact/overlap, incompatible units/frame, and a start epoch mismatch.
- Collision handling uses REBOUND's swept-line collision search with halt resolution. Event time and
  state are documented as internal-step precision, not an exact mathematical first-contact proof.
- Close-encounter observations do not modify dynamics. Escape events are named `ESCAPE_CANDIDATE` and
  require a boundary crossing plus configured outward-velocity, positive two-body-specific-energy,
  and persistence checks; they do not prove permanent escape in a four-body system.
- Trajectories are streamed as JSON Lines into a temporary directory. The complete archive is committed
  atomically and records input/settings hashes, REBOUND/Python/NumPy/project/constants versions, events,
  diagnostics, and explicit `COMPLETED|COLLISION|STOPPED|FAILED` status.
- Conservation reports use the scaled energy denominator `max(|E0|, |K0|+|U0|)`. Traditional relative
  energy error is present only when `|E0|` is safely separated from zero. Collision-terminated runs mark
  isolated-system conservation interpretation as not applicable after contact.

