# P0-M0/M1 implementation decisions

## ADR-M71-01：大气保留先验与视觉光学和权威轨道分层（2026-11-18）

- `stochastic_conics/0.4.3` 使用独立随机子流生成行星周期和大气先验；冻结 0.4.2 固定种子
  世界哈希，避免新抽样扰动历史轨道。
- 大气存在不能由质量/自转唯一推导。实现记录瞬时辐照、旋转修正逃逸速度、Jeans 参数代理、
  显式发生率和随机抽样，结果标记 `is_observation=false`。
- 纬向风只输出有种子、受声速及热力/地转参考尺度约束的范围；真空返回空值。
- 三波段大气光学负责地面观察的相对天空色与直达光消光，不进入 M3/C0/C1 权威历史，
  不宣称逐线光谱、云、折射或多次散射。
- 地面动画只遍历 API 返回的精确 M5A 采样。显示速度、请求频率和 HDR 曝光均不改变物理时间。

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

## P0-VIS2 — 2026-11-16

- VIS2 verifies M3 world ID, initial-state hash and exact M2-manifest hash before linking archives. Its
  authoritative range is the strict M2/M3 intersection; missing intervals are never extrapolated.
- A single JavaScript controller owns time, playback, camera, event seek and all four Plotly panels.
  Piecewise-linear orbit and thermal interpolation is explicitly display-only and does not modify source
  archives or claim a new physical solution.
- Collision timelines stop at the final archived scientific sample. Escape candidates remain nonterminal
  and continue when subsequent M2/M3 data exists. Optional M4 data must match both M2 and M3 hashes.
- Frame selection retains endpoints/events and is bounded for up to 100,000 input samples. Plotly is
  embedded by default; CDN use is explicit. JSON script data escapes markup characters and event details
  are assigned through `textContent`.

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

## 0.4.0 — 2026-11-16

- G2 is a new discriminated `stochastic_conics/0.4.0` configuration and generator. The legacy
  `GeneratorConfig` remains version `0.1.0`; no new defaults are injected into legacy resolved configs,
  preserving their canonical hashes and deterministic outputs.
- Each physical draw and each conic construction uses a fixed independent PCG64 substream derived from
  `SeedSequence([seed, candidate_index, stream_id])`. Replay values and distribution versions live in a
  separate generation metadata artifact; `WorldInitialState/0.1.0` is unchanged.
- Relative states use periapsis, eccentricity, explicit uniform true anomaly over the conic's valid
  domain, and isotropic angular-momentum directions. Version 0.4.0 supports elliptic and hyperbolic
  conics and explicitly rejects exact parabolic `e=1` rather than approximating it.
- Velocity kicks are bounded fractions of the constructed relative speed and are recorded as vectors.
  Metadata does not claim that pre-kick Keplerian elements describe the resulting instantaneous state.
- Initial spherical contact is a versioned, terminal `INITIAL_CONTACT`/`INITIAL_COLLISION` outcome at
  `t=0`; no IAS15 trajectory is fabricated. The existing mandatory M2 guard remains unchanged and still
  rejects contact independently of S1 policy.
- No hierarchy ratio, stability, survival, escape rejection, or habitability criterion is implicit in
  generation. Research screening reports unusual initial states without silently filtering them.

## P0-M4 — 2026-11-16

- M4 is a read-only consumer of initial-state, M2, M3 and optional G2 initial-contact artifacts. It
  requires matching world IDs and source hashes and never recomputes trajectories or temperatures.
- Scientific input status, dynamical outcome and multiple environmental labels are separate axes.
  `COMPLETED_OBSERVATION_WINDOW` is not long-term stability, and `ESCAPE_CANDIDATE` remains nonterminal.
- C0 exposure durations use piecewise-linear threshold crossing within archived intervals. Extrema are
  explicitly sampled extrema; integrated incoming energy uses trapezoidal integration. Both retain
  cadence-dependent uncertainty rather than claiming continuous exactness.
- Planet-involved and star-star collisions are distinct. No collision aftermath is synthesized. Initial
  contact produces an incomplete terminal report without requiring fabricated M2/M3 data.
- Scenario preference scores are reporting/ranking outputs only and never remove the raw world report.
## 0.4.1 — 2026-11-16

- G2.1 uses a separate strict `stochastic_conics/0.4.1` configuration. Version 0.4.0 accepts no new
  fields and retains its resolved hash, PCG64 entropy layout and generated states.
- Bounded generation checks the completed Cartesian state after velocity kicks and barycentric
  normalization. It never clamps a sampled velocity or distance. Attempts use independent
  `SeedSequence([seed, candidate_index, attempt, stream_id])` substreams and stop at `max_attempts`.
- Absolute planet speed is measured relative to the three-star COM. The selected-host escape proxy is
  an explicit two-body/concentrated-mass approximation; the combined-potential proxy is an instantaneous
  frozen-position speed scale. Neither proves permanent binding or future survival.
- Initial-contact policy is evaluated independently of every enabled distance, speed and escape-proxy
  bound. `allow_initial_contact=true` suppresses only the contact-policy rejection; a contact state is
  retained as the existing terminal t=0 outcome only when all other active bounds also pass. The M2
  guard remains unchanged and rejects every initial contact before IAS15.

## P0-M5A-01 — 2026-11-17

- Long-run contracts use schema `0.6.0` without changing M2 `0.2.0` archives or commands. The only
  authoritative physical clock is absolute REBOUND `simulation.t` in SI seconds; segment indices,
  output indices, wall-clock quotas and display speed are separate concepts.
- Output and segment times are calculated from global integer indices through decimal arithmetic, not
  repeated float addition. Segment zero owns the start sample; later segments exclude their left boundary
  so logical trajectory samples remain strictly increasing.
- Physics, deterministic schedule and execution policy have separate SHA-256 domains. Changing a wall
  quota does not masquerade as a physics change; changing output cadence or physical segment boundaries
  is resume-incompatible.
- Task state transitions are explicit and idempotent. `CANCELLED`, `COLLISION` and `FAILED` are distinct
  terminal states; no terminal state can return to `RUNNING`.
- A real REBOUND 4.6.0 experiment confirmed exact binary phase-space continuation on the tested platform,
  while also confirming Python callbacks are not serialized and must be reattached with external observer
  state. M5A-01 defines that state contract but does not yet claim a working production resume command.

## P0-M5A-02 — 2026-11-17

- Checkpoints use REBOUND's binary Simulationarchive representation, never rounded trajectory JSON.
  Binary byte length/SHA-256, external runtime-state SHA-256, source hashes, versions, platform, SI G,
  epsilon and canonical particle order are verified before callbacks are restored.
- Python collision and heartbeat callbacks are deliberately reattached after binary load. Close-encounter
  deduplication, escape start/recorded sets, emitted events, minimum distances and global diagnostic
  baseline/extrema are serialized separately with strict finite models.
- Checkpoints are accepted only at deterministic output or segment boundaries. They are written into a
  temporary directory, fsynced and atomically renamed; existing destinations are never overwritten.
- Same-version/platform continuation is bitwise equal in the covered short benchmark. This is not a
  cross-version portability promise or a universal long-chaos coordinate-identity claim.
- A checkpoint containing a terminal collision cannot resume. Corrupt binary/state hashes, incompatible
  versions/platforms, changed contract hashes and mismatched initial state are hard errors.

## P0-M5A-03/04 archive foundation — 2026-11-17

- Every segment is first written and fsynced in a sibling temporary directory, then atomically renamed.
  Only after that durable rename is the global manifest atomically advanced. A crash between those two
  steps is repaired by verifying and registering the durable segment; replay never appends to it.
- Segment zero owns the global start output, and each later segment owns only outputs after its left
  boundary. Commit validates every sample against the integer-index physical clock, so recovery cannot
  duplicate a boundary sample.
- Segment manifests hash all artifacts and chain to the previous segment manifest. Verification rejects
  missing, reordered, modified, unexpected or out-of-plan durable directories and non-finite JSON.
- The writer lock is created with exclusive filesystem semantics and an unguessable ownership token.
  It is never auto-stolen or deleted based on PID or wall time. Ownership changes prevent automatic
  release; stale-lock handling remains an explicit future audited command.
- Temporary orphan directories are reported but retained for inspection. Automatic deletion would lose
  crash evidence and could race an operator's recovery decision.

## P0-M5A-05/06 runner and validation — 2026-11-17

- The runner advances IAS15 only to integer-indexed outputs, configured simulated-time checkpoints and
  immutable segment boundaries. Wall time is evaluated only after a durable segment commit; it never
  becomes an integration target or changes the physics/schedule hashes.
- Checkpoints inside an uncommitted segment include its already-owned trajectory samples and the event
  stream offset, in addition to REBOUND and observer/diagnostic state. The global task atomically points
  to the latest active checkpoint, so a crash can resume inside a segment without dropping or duplicating
  samples and events.
- A segment-boundary checkpoint is referenced by the segment manifest. After commit, deterministic
  retention keeps the configured number of newest verified checkpoint directories; immutable segment
  manifests retain the historical checkpoint hashes.
- A collision may create a terminal checkpoint at the causal callback halt time even though it is not a
  scheduled output. That checkpoint is inspectable but cannot resume; no post-collision gravity is run.
- Execution-policy hash differences such as a wall quota do not invalidate the same physics/schedule
  checkpoint. Physics or deterministic target changes remain hard resume errors.
- The read-only `SegmentedTrajectoryReader` verifies the entire immutable chain, then streams samples and
  events in strict global time order. It does not recalculate or interpolate the stored orbit.

## P0-M5B-01/02 read-only prototype — 2026-11-17

- The first Web slice is local, loopback-only and read-only. It may inspect committed manifests and call
  backend configuration validation, but it exposes no task mutation endpoint or simulated controls.
- Physical time displayed by the console comes directly from archived task/manifest SI seconds. Browser
  playback is presentation state and is never sent back as an integration speed or world day length.
- Configuration validation reuses the CLI resolver on an already parsed JSON object. It cannot execute
  YAML tags, shell commands, Python expressions or user-selected filesystem paths.
- Artifact discovery accepts only direct non-symlink directories with allowlisted run IDs. The server
  binds only to loopback and emits a restrictive CSP; authentication is deferred because non-loopback or
  multi-user deployment is explicitly outside this prototype.

## P0-M7.2-a — 2026-11-18

- `m72_three_reservoir_v1` is a new authoritative dynamic-environment domain. It does not mutate or
  reinterpret M3 C0/C1 archives; model/config/source/result hashes make comparisons explicit.
- Surface, shallow-rock and ocean-mixed-layer sensible heat are integrated in SI. Linear reservoir
  exchange cancels in the global energy budget; an extra integrated external-energy state provides a
  direct conservation residual rather than an output-grid trapezoid proxy.
- M5A coupling accepts only a verified `COMPLETED` segment chain and computes exact-sample three-star
  irradiance. Between archived times it interpolates scalar flux linearly and makes no claim to recover
  unresolved close-passage peaks.
- JSON environment checkpoints are immutable, hash-bound restart states. Restarting `solve_ivp` at the
  checkpoint boundary is tolerance-consistent, not a serialization of RK internal stages or a promise
  of bitwise identity.
- Planet exploration schema v3 uses a 96x192 equal-area basin solve for water level, ten map contours,
  an observer marker and angular-radius star rendering. Terrain exaggeration, exposure and decorative
  star suppression are display-only and never feed scientific state.
