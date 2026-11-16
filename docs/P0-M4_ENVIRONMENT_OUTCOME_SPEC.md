# P0-M4 — Environment evolution and world-outcome classification
Status: design specification; dependence: merged M2/M3; integration with G2 stochastic generation where available.

## Product intent
**Every world can have a story**, including stable irradiation, extreme seasons, freezing, runaway heating in the simple model, stellar impact, planet collision, temporary ejection or unknown continuation. Do not make habitability or survival a global acceptance condition.

## Scientific separation and status contracts
Three independent axes:
1. `scientific_input_status`: S0 valid / numerical input unfit / initially contacting;
2. `dynamical_outcome`: RUNNING, COMPLETED_OBSERVATION_WINDOW, COLLISION_TERMINATED, INITIAL_CONTACT, ESCAPE_CANDIDATE, INTEGRATION_STOPPED, INTEGRATION_FAILED (these do not automatically imply planet destroyed or permanently escaped);
3. `environmental_outcome`: FROZEN_EXPOSURE, HEAT_EXPOSURE, HIGH_IRRADIANCE_VARIABILITY, TEMPERATE_INTERVAL, DATA_INSUFFICIENT, THERMAL_SOLVER_FAILED, etc. **These are configurable diagnostic labels, not proved climate phenomena**.
Store axes separately; multiple environmental labels may coexist. `COMPLETED` means completed observational interval, not orbit stable forever.

## Physics scope and implementation
- M4-E0: Consume and validate G2/M2/M3 manifests, `trajectory.jsonl`, `events.json`, `stellar_flux.jsonl`, `c0_temperature.jsonl`, `c1_latitude_temperature.jsonl`, `energy_budget.json`; match world_id, t0, overlapping simulation interval, hash references where available, SI/K/W/m² units, monotonic time; never silently combine unrelated or different epochs.
- M4-E1: Time-series environmental exposures based on **M3 already-calculated** irradiation and C0/C1 temperatures. Track extrema, duration above/below configurable bounds, thermal variation rate, fraction of latitude bands above/below bounds, longest continuous episode, integrated radiative energy, and clear coverage windows. Distinguish sampled observations from interval-integrated estimates, with optional bounded interpolation policy. Do not claim local weather from longitude-averaged C1.
- M4-E2: Build a timeline of dynamic and environmental events: stellar collisions, planet collisions, initial-contact terminal, close encounters, escape candidates, rapid irradiation changes, thermal exposure thresholds. Derived M4 event timestamps need sampling uncertainty and detection method metadata.
- M4-E3: World reports and categories. No hard "habitable" gate. Each category is configurable, explainable, auditable; include flags such as `planet_status=unresolved`, `collision_with_planet`, `stellar_collision`, `ejection_candidate`, `thermal_exposure_extreme`. Do not assert a collision has destroyed the planet when it involved only two stars. Model must separate *physical observation* from *game narration layer*.
- M4-E4: Optional scenario selection for player experience (e.g. gentle, chaotic, catastrophic) via **reporting/ranking**, never rejection of valid scientifically representable worlds. Keep raw candidate list accessible; selection weights are user preferences not laws of nature.
- M4-E5: Tests and end-to-end demonstrations for a stable example, chaos/close encounter, escape candidate, stellar-stellar collision, planet collision, initial contact (when G2 available), and input failure. Reference actual M2 event semantics; do not fabricate unsupported collision aftermath.

## Interface proposal
```text
threebody world assess --initial ... --m2-archive ... --m3-archive ... --config configs/m4_world_outcomes.yaml --out-dir runs/m4-example
```
Use a separate `m4` config/schema v0.4.0. Model examples:
```yaml
schema_version: "0.4.0"
classification:
  cold_threshold_k: 240.0
  hot_threshold_k: 330.0
  persistence_min_s: 86400.0
  high_flux_variation_fraction: 0.5
  require_complete_thermal_window: false
selection:
  survival_required: false
  include_collision_terminated: true
  include_escape_candidates: true
  include_unstable_worlds: true
```
Threshold values are illustrative, **not scientific habitability thresholds**. Never infer liquid water or atmosphere from C0 grey-body temperature alone.

Output: `world_outcome_manifest.json` (all model/input versions/hashes), `environment_events.json`, `environment_statistics.json`, `world_classification.json`, `world_timeline.jsonl`, `world_assessment_summary.json`.

## Collision/thermal lifetime policy
- M2 collision stop closes reliable orbital interval. Existing M3 can only propagate to M2 termination time by explicit opt-in; M4 must not extrapolate beyond it.
- For planet impact, terminal status; no future planet temperature; **no arbitrary planet destruction VFX as physics**.
- For star-star impact, future three-star M2 trajectory is unavailable; report incomplete environmental history. Do not assert planet also collides.
- For escaped planet, keep tracking actual REBOUND trajectory until integrated horizon; escape candidate alone does not terminate.
- For t0 overlap/contact, separate G2 terminal outcome; ordinary M2/M3 archive may be absent. M4 must handle absent thermal data as NOT_AVAILABLE, not corrupted or automatically rejected.

## Quantitative validation
- Source event IDs, type, coordinates/uncertainty, and start/end times can be reproduced.
- Exposure durations must be bounded by available interval and invariant under supported refinement within tolerance. Avoid counting an entire wide interval just because one end exceeds a threshold.
- Energy closure is inherited from M3 and must be reported; cannot rewrite or mask failed budgets.
- Tests confirm same input + config -> same scientific fields regardless of creation timestamp.
- Neither initial-overlap S1 toggle nor "survival_required=false" disables M2 numerical guards.
- Honest unavailable/failed/partial outcomes; sampled min/max vs continuous extrema differentiated.
- Existing M0/M1/M2/M3/VIS1 CLI and file schema unaffected.

## Implementation phases
M4-01 data adapter + source integrity and windows;
M4-02 threshold/persistence exposure event engine;
M4-03 dynamic/environmental world outcome categories, initial-contact and missing-thermal support;
M4-04 scenario ranking/reporting CLI and atomic archive;
M4-05 physical/test fixtures, CI, `docs/development/M4_REPORT.md`; Draft PR, no merge without review.

Longer-term climate physics (atmospheric circulation, oceans, moist processes) is **outside M4-P0**. This iteration deals in defensible environmental signals and story outcomes on top of M3, not an unvalidated weather engine.
