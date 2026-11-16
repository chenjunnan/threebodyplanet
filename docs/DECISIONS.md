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

