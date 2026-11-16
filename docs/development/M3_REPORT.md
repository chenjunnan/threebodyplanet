# P0-M3 development report

Date: 2026-11-16  
Branch: `feature/p0-m3-radiation-thermal`  
Design baseline: `design/p0-m3-radiation-thermal` at `4526447`

## Delivered milestones

### M3-01 — data boundary and M2 trajectory input

- Added strict version 0.3.0 YAML models and canonical configuration SHA-256.
- Added a read-only M2 archive auditor for initial-state hash, manifest status, sample count, canonical
  body order, finite data, strict time ordering, SI/frame compatibility, and first-sample identity.
- `COMPLETED` is accepted by default; `COLLISION` requires explicit opt-in; `STOPPED` and `FAILED` are
  rejected. No trajectory is extrapolated beyond its archived interval.
- Added piecewise cubic Hermite interpolation using M2 position and velocity. It exactly reproduces
  nodes and first derivatives. Maximum cadence and a midpoint Hermite-versus-linear relative diagnostic
  are archived; the latter is explicitly not an error bound.

### M3-02 — three-star radiation and rigid rotation

- Added independent `L/(4*pi*d^2)` bolometric flux records for all three stars, directions from the
  planet to each star, total center flux, and the ideal spherical `F/4` average.
- Production input requires explicit finite positive `luminosity_w` for every star. Star/planet physical
  contact invalidates radiation; no missing luminosity receives a default.
- Added a strictly validated right-handed rotation basis, positive/negative/zero rotation rates,
  large-epoch decimal phase reduction, local day/night projection, analytic zonal mean illumination,
  and finite-volume latitude-band quadrature including polar day/night.

### M3-03 — C0 global energy balance

- Added SciPy RK45 integration of temperature plus accumulated absorbed, internal, and emitted energy.
- Output distinguishes planet-center flux, global incoming flux, absorbed flux, emission, and net
  heating. Sampling cadence remains independent of the adaptive integration step.
- Fixed benchmarks cover analytic grey-body equilibrium, analytic zero-flux cooling, thermal inertia,
  step-size convergence, positive Kelvin enforcement, and integrated energy closure.

### M3-04 — C1 latitudinal energy balance

- Added equal-area `x=sin(latitude)` cells with finite-volume diffusion and exact zero polar face flux.
- Diffusion is integrated with SciPy BDF. Its global signed contribution is accumulated and reported;
  no external forcing case conserves area-weighted heat while reducing temperature variance.
- Tests cover independent columns at `D=0`, global `F/4` consistency, polar illumination, dynamic
  energy closure, and 18/36-band resolution convergence.

### M3-05 — CLI, archive, schema, CI, and compatibility

- Added `threebody world thermal --config ... --out-dir ...` and an atomically committed archive with
  stellar forcing, C0/C1 temperatures, energy budgets, resolved settings, and a traceable manifest.
- Known solver failures archive as `FAILED` and never as `COMPLETED`; JSON/JSONL forbids NaN/Infinity.
- Added `configs/m3_thermal.yaml`, `schemas/thermal.schema.json`, SciPy `>=1.11,<2`, and CI import/testing
  on Python 3.11/3.12. Existing generation and M2 simulation commands and schemas are unchanged.

## Verification record

Development environment: Conda Python 3.11.18, NumPy 2.4.6, SciPy 1.17.1, Pydantic 2.14.3,
PyYAML 6.0.3, REBOUND 4.6.0, Ruff 0.17.5.

```text
pytest -q
81 passed in 2.48s

ruff check .
All checks passed!

python -m compileall -q src tests
completed with exit code 0
```

The complete local test suite used 2.84 seconds wall time (2.48 seconds reported by pytest). The M3 CLI integration test runs a
real short IAS15 archive followed by both thermal solvers and validates all six M3 artifacts.

## Quantitative scientific benchmarks

- C0 Earth-like no-greenhouse equilibrium for `F=1361 W/m2`, `A=0.3`, emissivity 1 and `Q=0`:
  `254.5781401165717 K`.
- C0 analytic zero-flux cooling benchmark: energy residual `-4.410743713378906e-06 J/m2`, normalized
  residual `1.4702479044596354e-15` relative to the maximum heat-content/energy-flow scale.
- C1 36-band dynamic benchmark: energy residual `2.752058207988739e-07 J/m2`, normalized residual
  `2.7515920528395887e-17` on the same scale.
- C1 signed global meridional transport energy and maximum instantaneous global transport imbalance
  were both `0.0` in the 36-band reference after conservative last-cell roundoff closure.

These values are deterministic reference-test results for the recorded environment, not universal
accuracy guarantees for chaotic orbital forcing or arbitrary climate parameters.

## Scientific limits and follow-up risks

- M3 is a grey-body energy-balance reference model, not weather, atmospheric circulation, ocean,
  precipitation, storm, or habitability prediction.
- Stellar emission is isotropic and bolometric. There are no spectra, limb darkening, finite disks,
  eclipses/occultations, atmospheric absorption, clouds, or greenhouse-gas radiative transfer.
- The rotation axis is fixed; tides, spin evolution, obliquity evolution, and precession are absent.
- C1 is longitude averaged. It cannot represent day/night temperature contrast or longitudinal flow.
- Hermite interpolation honors M2 positions and velocities at output nodes but does not recreate the
  N-body trajectory. The archived midpoint diagnostic is not a rigorous error estimate. Close encounters
  or large corrections require a new M2 run with shorter output intervals.
- Collision-terminated archives can be used only by explicit opt-in and only through their actual final
  sample. No post-collision radiation or temperature is generated.
- The current reader is bounded in-memory and suitable for prototype archives. A windowed streaming
  reader is required before multi-hundred-GB production trajectories.
