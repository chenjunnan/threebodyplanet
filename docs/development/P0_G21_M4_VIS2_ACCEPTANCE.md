# P0 G2.1 → M2 → M3 → M4 → VIS2 acceptance record

Date: 2026-11-17

## Inputs under review

The acceptance tree was assembled only in a temporary local clone from `origin/main` plus the exact
Draft PR heads. No integration merge was pushed and none of the Draft PRs was merged.

| component | PR | reviewed head |
|---|---:|---|
| G2.1 bounded initials | #5 | `0ecd2d8` (`641618e` code fix) |
| M4 world outcomes | #6 | `583ba3e` |
| VIS2 linked dashboard | #7 | `908c677` |

The combined tree ran 144 tests successfully. `ruff check .` and
`python -m compileall -q src tests` both exited successfully.

## Complete nominal flow

1. G2.1 generated `candidate-000000` with the bounded preset. Selection took two deterministic attempts;
   the first was rejected for `COMBINED_POTENTIAL_ESCAPE_FRACTION_ABOVE_MAX` and the second satisfied all
   active bounds. There was no initial contact.
2. M2 integrated the unmodified Cartesian state with REBOUND 4.6.0 IAS15 from 0 to 172,800 s. The archive
   was `COMPLETED` with nine samples and no runtime events. Maximum scaled energy error was
   `1.8631063046363532e-16`; maximum angular-momentum relative error was
   `3.622597295829893e-16`.
3. M3 ran the real RK45 C0 and BDF C1 models at the same nine physical times. The Hermite-versus-linear
   midpoint diagnostic was `1.0297597293278978e-4`; as specified, this is not an orbit-error bound.
4. M4 consumed the matching source hashes and returned `COMPLETED_OBSERVATION_WINDOW` plus
   `TEMPERATE_INTERVAL` for this short diagnostic window.
5. VIS2 verified M2/M3/M4 provenance and exported an embedded-Plotly offline dashboard with nine linked
   frames. The generated file was 4,865,820 bytes. Generated archives and HTML remained temporary and
   are not committed.

## Required adverse scenarios

### High-speed escape

An unrestricted G2.1 configuration generated twelve genuine hyperbolic planet initial states
(`e=3`, barycentric host, zero added velocity kick). Candidate zero was integrated by IAS15 for 43,200 s.
M2 completed thirteen samples and emitted one nonterminal `ESCAPE_CANDIDATE`; M4 preserved that exact
uncertainty label and reported missing thermal data separately as `DATA_INSUFFICIENT`. No permanent
escape claim was made.

### Initial planet collision

A deterministic G2.1 state placed the planet 1,000,000 m from `star-1`, producing one archived terminal
initial-contact outcome without invoking M2. M4 reported:

```text
dynamical_outcome=INITIAL_CONTACT
collision_with_planet=true
stellar_collision=false
planet_status=collision_involved
```

### Initial stellar collision

A separate deterministic state placed `star-1` and `star-2` 1,000,000 m apart while keeping the planet
away from the pair. M4 reported:

```text
dynamical_outcome=INITIAL_CONTACT
collision_with_planet=false
stellar_collision=true
planet_status=unresolved
```

Neither initial-contact path fabricated an M2 trajectory or postcollision temperature.

### Missing thermal model data

Two real M3 archives were generated from the nominal M2 archive:

- `thermal.mode=c0`: nine flux and C0 samples, no C1 samples. M4 could use the available global model and
  returned `TEMPERATE_INTERVAL` for the observed window.
- `thermal.mode=c1`: nine flux and C1 samples, no C0 samples. M4 returned `DATA_INSUFFICIENT` and did not
  emit `TEMPERATE_INTERVAL`; latitude bands are not silently substituted for the absent C0 model.

## Conclusion

The reviewed fixes behave correctly both in isolated regressions and across real generated, integrated,
thermal, classified and visualized artifacts. This is bounded acceptance evidence for the tested
configurations, not proof of long-term stability, permanent escape, habitability or exact collision time.
