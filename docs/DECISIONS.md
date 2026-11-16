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
- Collision handling uses REBOUND's swept-line collision search; a custom resolver captures the causal
  particle indices and requests a stop without mutating state. Event time and state are documented as
  internal-step precision, not an exact mathematical first-contact proof.
- Close-encounter observations do not modify dynamics. Escape events are named `ESCAPE_CANDIDATE` and
  require a boundary crossing plus configured outward-velocity, positive two-body-specific-energy,
  and persistence checks; they do not prove permanent escape in a four-body system.
- Trajectories are streamed as JSON Lines into a temporary directory. The complete archive is committed
  atomically and records input/settings hashes, REBOUND/Python/NumPy/project/constants versions, events,
  diagnostics, and explicit `COMPLETED|COLLISION|STOPPED|FAILED` status.
- Conservation reports use the scaled energy denominator `max(|E0|, |K0|+|U0|)`. Traditional relative
  energy error is present only when `|E0|` is safely separated from zero. Collision-terminated runs mark
  isolated-system conservation interpretation as not applicable after contact.

## 0.2.0 review corrections — 2026-11-16

- Collision participants are captured from REBOUND's `collision_resolve` callback `p1/p2` indices.
  The callback records causality and requests a stop without changing particle state; no terminal-state
  nearest-pair inference is permitted.
- `max_wall_seconds` uses REBOUND's user-stop path and produces `STOPPED`, a partial valid trajectory,
  and CLI exit code 1. It is distinct from both collision and solver failure.
- The mandatory input guard evaluates initial mechanical diagnostics and representable pair separation/
  acceleration limits. Finite values outside the float64 mechanics domain are rejected before REBOUND.
- Pair-distance archives use `minimum_observed_pair_distances_m` and explicitly identify internal-step
  endpoint semantics. They are not continuous-trajectory minima or exact threshold-crossing times.

## P0-VIS1 — 2026-11-16

- VIS1 is a read-only consumer of the existing M2 `TrajectorySample`, `SimulationEvent`, and
  `IntegrationResult` contracts. It never imports M3, changes REBOUND state, or recomputes an orbit.
- Plotly is isolated in the optional `viz` dependency extra and imported lazily. Existing generation,
  simulation, and thermal commands remain usable without Plotly.
- Archived positions remain authoritative SI meters. Display conversion uses the exact defined AU value
  `149597870700 m`; the page identifies `BARYCENTRIC_INERTIAL` and uses equal 3D axis scaling.
- Event display positions use only declared `event.body_ids` and piecewise-linear sampling interpolation.
  They are labeled estimates, not exact collision/contact locations or new physics results.
- Animation frames update only four current-position traces. Static paths are stored once; endpoints and
  event-adjacent samples are mandatory when frame downsampling is requested.
- `STOPPED` and `FAILED` archives with complete samples are explicitly labeled partial. Empty failed
  archives, inconsistent counts/times/IDs, out-of-range events, non-finite data, and oversized input are
  rejected rather than repaired or extrapolated.

## 0.3.0 — 2026-11-16

- M3 is a one-way consumer of version 0.1.0 initial states and version 0.2.0 M2 archives. It does not
  modify REBOUND state, N-body settings, or any existing M0/M1/M2 field meaning.
- Orbit positions between M2 output nodes use piecewise cubic Hermite interpolation constrained by
  stored positions and velocities. The midpoint Hermite-versus-linear difference is recorded only as
  a diagnostic, not a rigorous interpolation error bound; configured cadence excess is rejected.
- Stellar radiation uses three positive, explicit `luminosity_w` values and isotropic bolometric
  `L/(4*pi*d^2)` point sources. Occultations, spectra, and finite stellar disks remain unimplemented.
- Rotation inputs must already form a unit, orthogonal right-handed basis. M3 never silently normalizes
  them. Large-epoch phase reduction uses decimal modulo arithmetic before returning to float64.
- C0 uses SciPy RK45. C1 uses an equal-area `x=sin(latitude)` finite-volume grid, zero polar flux, and
  SciPy BDF. Absorbed, internal, emitted, and transport energies are integrated alongside temperature
  so archived energy residuals are independently visible.
- C1 forcing is longitude averaged and finite-volume-band averaged. C0/C1 are grey-body energy-balance
  reference models, not local weather, atmospheric circulation, or habitability proofs.

