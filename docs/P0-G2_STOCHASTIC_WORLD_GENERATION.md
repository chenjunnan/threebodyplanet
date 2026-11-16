# P0-G2 — Fully stochastic four-body initial-state generation
Status: design approved for implementation; base: current main including M2/M3/VIS1.

## Intent
Generate diverse **three massive stars plus one massive rocky planet** without imposing long-term survival. Planet escape, collision, stellar collision, failed thermal evolution, hot/cold extremes and temporary stability are interesting outcomes. However, S0 data/finite/units/schema invariants, numerical integrator safety, and honest event semantics remain mandatory. No scripted orbits.

## Compatibility
- Preserve all v0.1.0 generator configurations and deterministic byte-level output for the same runtime/NumPy/seed/config: `cartesian` and `hierarchical_orbit` remain unchanged.
- Add an explicitly versioned generator `stochastic_conics` / generator v0.4.0 with a versioned config schema/adapter; do not extend the old `Literal["0.1.0"]` in a way that silently changes old parsing or config hashes.
- `WorldInitialState` stays with canonical IDs `star-1`, `star-2`, `star-3`, `planet-1`, SI, `BARYCENTRIC_INERTIAL`; M2 and M3 must read old and new generated states.
- Use independent random substreams `SeedSequence([seed,candidate_index,stream_id])`, PCG64; persist sample draws/host choices/distribution versions to separate `generation_metadata.json` per candidate or similarly versioned artifact, without changing the old body schema.
- Star masses / rocky planet mass / density remain configurable physically bounded distributions. M3 luminosity from generated physical profiles remains present and explicitly versioned.

## New stochastic parameters (ALL individually configurable)
1. Inner stellar pair periapsis/separation scale range (SI m), sampled (uniform or log-uniform).
2. Third star orbit about the inner stellar-binary COM, stochastic scale (SI m) without a forced minimum ratio beyond **numerical safety**. It may pass inside the inner binary.
3. Planet initial orbit scale about selected host `star-1`, `star-2`, `star-3` or stellar barycenter, with weighted random host choice and user weights.
4. Eccentricity distribution for EACH of inner pair, outer stellar relative orbit, and planet relative orbit. Support bound `0≤e<1` and unbound `e>1` modes. If supporting `e=1` parabolic mode, implement explicitly; otherwise reject with clear reason. Do not treat hyperbolic states as closed orbits.
5. Full 3D orientation: isotropic orbital angular-momentum direction (`cos(i)` uniform on [-1,1] for isotropy), random longitude of ascending node, argument of periapsis, and true anomaly (physically valid for each conic). Angular distribution choices configurable; values/units explicit.
6. **Velocities**: compute physically coherent Cartesian velocities from conic elements, then optionally apply a deterministic, bounded three-vector velocity kick with configurable distribution/magnitude to obtain chaotic/unbound conditions. Record kick vectors, and do not pretend resulting states retain original Keplerian elements.
7. Optional fully unconstrained SI Cartesian random position/velocity distribution mode (can extend legacy `cartesian` via NEW generator version; old algorithm unchanged), including anisotropy and correlations when specified.

## Correct orbital-element-to-Cartesian construction
Represent each relative two-body initial conic by periapsis distance `q>0`, eccentricity `e≥0`, orientation and true anomaly `f`; `p=q(1+e)`, `r=p/(1+e cos(f))`, `h=sqrt(G*(m1+m2)*p)`. In the orbital perifocal basis:
`r_vec = r*(cos(f)*p_hat + sin(f)*q_hat)`;
`v_vec = (G*(m1+m2)/h)*(-sin(f)*p_hat+(e+cos(f))*q_hat)`.
Reject invalid `1+e cos(f)≤0`, zero/overflow distance, unrepresentable masses/velocities. For elliptical `e<1`, sample f or mean anomaly with the distribution **explicitly named**. Mean anomaly sampled uniformly corresponds to a different spatial density than true anomaly uniform; do not conflate.
Embed relative states with barycentric momentum and position conservation; combine stellar binaries and planet host relative state coherently, including planet recoil. Finally shift the four-body system to COM origin. Do not freeze or reconstruct trajectories after generation; M2 REBOUND evolves them.

## Example config (illustrative bounds, NOT a stability promise)
```yaml
generator:
  type: stochastic_conics
  version: "0.4.0"
  stars:
    mass_solar: {min: 0.3, max: 2.5, distribution: log_uniform}
  planet:
    mass_earth: {min: 0.1, max: 8.0, distribution: log_uniform}
    density_kg_m3: {min: 2800.0, max: 8500.0, distribution: uniform}
  geometry:
    inner:
      periapsis_m: {min: 1.0e10, max: 2.0e12, distribution: log_uniform}
      eccentricity: {min: 0.0, max: 1.8, distribution: uniform}
    outer:
      periapsis_m: {min: 2.0e10, max: 2.0e13, distribution: log_uniform}
      eccentricity: {min: 0.0, max: 2.0, distribution: uniform}
    planet:
      periapsis_m: {min: 2.0e9, max: 5.0e12, distribution: log_uniform}
      eccentricity: {min: 0.0, max: 2.2, distribution: uniform}
      host_weights: {"star-1": 1, "star-2": 1, "star-3": 1, "barycenter": 1}
    orientation: {normal: isotropic, node: uniform, periapsis_argument: uniform}
    anomaly: {distribution: true_anomaly_uniform_on_valid_domain}
    velocity_kick:
      distribution: isotropic_speed_fraction
      speed_scale_fraction: {min: 0.0, max: 0.6, distribution: uniform}
```
Exact schema implementation may adjust nesting, but **all parameters, distribution choices, units, and version identifiers must be serialized and documented**. These ranges are broad exploration examples, not verified astrophysical priors.

## Critical policy: initial collision vs runtime collisions
- **S0 cannot be disabled**. S1 initial overlap/clearance and S2 suitability filters remain configurable and may be disabled or set report-only. No default filter for ejection, unstable orbits, or lack of habitability.
- The current M2 solver-domain guard **rejects initial overlap/contact** even if S1 is disabled. Do not bypass it or submit an overlap to point-mass IAS15. Instead represent initial contact as an explicit `INITIAL_CONTACT` / `INITIAL_COLLISION` t0 terminal outcome through a separate event/terminal-world pathway, with initial state preserved and no false integration beyond t0. Version the outcome event schema; VIS2 displays it. It is a valid GAME outcome but not a valid ordinary IAS15 integration initial state.
- After t0, preserve the M2 true collision halt and `ESCAPE_CANDIDATE` semantics. Star collision is also allowed; do not simulate a merger or explosion unless a separate physical model is later implemented. Mark unsupported post-collision states TERMINAL / UNMODELED rather than inventing trajectories.
- M2 currently does **not** terminate when a planet merely flies away. Configured escape-candidate detection should inform classification, not forcibly reject generation or imply permanent escape.

## Implementation tasks
G2-01 New strict versioned configs + distributions + replay metadata, without breaking legacy configs/hashes.
G2-02 Conic state builder, host stochastic selection, independent 3D orientation, eccentric/unbound modes, velocity kicks and barycenter normalization.
G2-03 Generation policy distinction (S0 vs S1 vs initial-contact terminal vs M2 solver input); don't weaken solver guards.
G2-04 CLI batch generation of many scenarios, summary statistics of host/eccentricity/distance/velocity/invalid/terminal counts. Keep every candidate's reason code.
G2-05 Tests: same seed identical; candidate order independent; legacy golden outputs unchanged; distributions within bounds; isotropic orientation; eccentric/hyperbolic specific energy and two-body angular momentum; four-body barycenter/linear-momentum close to zero; positive/finite units; initial contact is not incorrectly run as IAS15; non-survivors not silently filtered; event metadata no false stable-orbit claims.

## Deliverables
`configs/g2_chaotic_worlds.yaml`; new generator module and strict config; `generation_metadata.json`; reproducible batch data; `docs/development/G2_REPORT.md` with real tests and scientific limits; Draft PR targeting main, no auto-merge.
