# P0-M5A development report

Date: 2026-11-17
Branch stack: `feature/p0-m5a-contracts` → `feature/p0-m5a-checkpoints` →
`feature/p0-m5a-segments-recovery` → `feature/p0-m5a-runner-cli`
Current scope: M5A-01 through M5A-06

## Implemented

- Integrated the approved M5 master roadmap, M5A scientific addendum, M5B console specification and
  long-horizon design onto the latest main containing G2.1, M4 and VIS2.
- Added strict `0.6.0` long-run configuration models for physical time, deterministic segmentation,
  checkpoint policy, resource limits, resume compatibility and existing IAS15/event settings. Unknown
  fields and non-finite values are rejected.
- Added `PhysicalClock`, which derives output and segment targets from global integer indices using
  decimal arithmetic. It includes a true non-divisible final endpoint without accumulated addition drift.
- Added segment index contracts with contiguous coverage and unique sample ownership: segment zero owns
  the global start; every later segment is left-open and right-closed.
- Added separate physics, schedule and execution-policy hashes, checkpoint/run manifest models, and an
  explicit idempotent task state machine including `PAUSING`, `PAUSED`, cancellation and distinct physical
  or numerical terminal states.

The first three stacked Draft PRs separate contracts, binary checkpoints and archive recovery. The fourth
adds the real runner and CLI. No background worker or Web control is included; a UI must not advertise an
asynchronous pause button until M5B has a server-authoritative task service.

## M5A-02 checkpoint foundation

- `save_checkpoint` accepts only deterministic output/segment times, writes REBOUND binary state plus
  strict external observer/diagnostic state, fsyncs all artifacts and atomically renames a new directory.
- `load_checkpoint` verifies byte size and SHA-256 before REBOUND parsing, enforces initial/physics/
  schedule hashes, version/platform policy, SI G, IAS15 epsilon, canonical particle order and masses/radii.
- REBOUND function pointers are explicitly reattached. Encounter deduplication, escape persistence,
  emitted events, minimum distances and the original conservation baseline/maxima survive recovery.
- Terminal collision checkpoints are inspectable artifacts but are rejected by the active resume path.
  Existing checkpoint directories cannot be overwritten.
- Corrupt binary and observer state, altered versions/platform, changed contract hashes, unsafe physical
  times and observer error states have explicit rejection tests.

## M5A-03/04 immutable archive and recovery foundation

- New run archives are created without overwrite through a sibling temporary directory and contain the
  exact initial state, resolved long-run configuration, global manifest, segment/checkpoint stores and
  lock namespace.
- A single-writer lock uses exclusive creation plus a run-scoped random token. An existing lock is never
  inferred stale from PID or wall time and never removed automatically.
- Completed segment artifacts are fsynced, atomically renamed and then registered in the global manifest.
  Each manifest hashes trajectory, events and diagnostics and chains to its predecessor.
- Every trajectory sample is checked against the authoritative integer-index output schedule. Segment zero
  includes the global start; later segments exclude their left boundary, preventing replay duplicates.
- Recovery registers a durable segment left behind by a crash after rename but before global-manifest
  update. Repeated recovery is idempotent. Temporary orphan directories are reported and preserved.
- Verification rejects corrupt hashes, broken chains, missing or unexpected files, plan-external segment
  directories and non-finite structured JSON. Existing committed segments cannot be overwritten.
- Failure hooks exercise artifact-write, pre/post-rename and pre/post-global-manifest interruption points.

## M5A-05/06 runner, adapter and CLI

- The segmented runner uses the existing four-body REBOUND IAS15 construction and M2 guard. It integrates
  only to deterministic output, simulated-time checkpoint and segment targets. Wall quotas are checked
  only after a durable segment boundary and therefore cannot alter adaptive-step history.
- Monthly (or otherwise configured) in-segment checkpoints contain REBOUND state, global observer and
  conservation state, pending segment samples and its event offset. An atomic task pointer selects the
  latest safe active checkpoint. A failure after checkpoint creation replays without duplicate output.
- Segment-boundary checkpoints are linked into immutable manifests. Retention keeps the configured newest
  checkpoint directories; historical manifests keep their hashes. Total binary plus observer-state bytes
  are checked against the resource limit.
- A causal collision commits one terminal segment and a non-resumable terminal checkpoint at the callback
  halt state. No later segment or collision aftermath is synthesized.
- `SegmentedTrajectoryReader` verifies the chain and streams a range of existing `TrajectorySample` values
  and events without recomputation. CLI commands start, safely resume, verify and inspect checkpoints.
- A wall-limit override changes invocation policy only. Physics and schedule hashes remain immutable.

## REBOUND IAS15 binary recovery experiment

The experiment used REBOUND 4.6.0 and a real G2.1 `candidate-000000` four-body state from the final main
acceptance run. IAS15 integrated at deterministic targets 21,600 and 43,200 s, saved a binary checkpoint,
loaded it into a new `Simulation`, reattached a Python heartbeat, and continued both uninterrupted and
restored simulations to 64,800 and 86,400 s.

Observed results:

```text
checkpoint bytes:                         8,684
t, dt, G, IAS15 epsilon:                  equal after load
particle hash order:                      equal
checkpoint phase space:                   bitwise equal; max absolute difference 0
checkpoint total energy:                  equal
continuation at 64,800 s:                 bitwise equal; max difference 0
continuation at 86,400 s:                 bitwise equal; max difference 0
Python heartbeat serialized:              false
REBOUND load warning:                     function pointers must be reset
```

This establishes exact continuation for this short same-version/same-platform case. It does not promise
cross-version binary portability or long-horizon coordinate identity in a chaotic system. REBOUND does
not restore Python callbacks; M5A-02 must persist and restore observer/diagnostic state explicitly.

## Final-main integration baseline

Before M5 work, final main `6c1e443` passed 144 tests, Ruff and compileall. A fresh real flow generated a
bounded G2.1 state, integrated nine IAS15 samples over 172,800 s, ran nine M3 C0/C1 samples, produced an
M4 finite-window classification and exported a nine-frame offline VIS2 dashboard. M2 maximum scaled
energy error was `1.8631063046363532e-16`.

## 100-year M5A scientific validation

The final runner used the real bounded G2.1 acceptance state, REBOUND 4.6.0, IAS15 epsilon `1e-9`, daily
outputs, yearly immutable segments and monthly simulated-time checkpoints for 100 Julian years. This is a
finite integration benchmark, not proof of long-term stability or habitability.

```text
physical coverage:                       0 .. 3,155,760,000 s
segments / logical samples:              100 / 36,526
strictly increasing times:               true
status / events:                         COMPLETED / 0
maximum scaled energy error:             1.9252098481242316e-15
maximum angular-momentum relative error: 2.025093452145639e-15
retained checkpoints:                    3
archive size:                            48 MiB
wall time / peak RSS:                    109.06 s / 106,880 KiB
full verification wall time:             4.88 s
```

Adding monthly checkpoints produced all 36,526 logical samples exactly equal to the otherwise identical
boundary-checkpoint run, demonstrating that checkpoint writes add no physical integration target beyond
the already hashed monthly schedule. A second 100-year run was split into ten invocations with nine real
binary restores. It completed in 118.93 s and all 36,526 logical samples were exactly equal to the
continuous final-runner archive on this same REBOUND/platform combination. The comparison process loaded
both full trajectories and therefore reached about 329 MiB RSS; the runner itself retains only one
segment and the configured checkpoint history.

## Verification

Executed with the Python 3.11 Conda environment:

```text
pytest -q
........................................................................ [ 42%]
........................................................................ [ 84%]
...........................                                              [100%]
193 tests collected; exit code 0

ruff check .
All checks passed!

python -m compileall -q src tests
exit code 0
```

Fourteen M5A-01 tests cover strict loading, finite resource bounds, non-divisible and fractional physical
time schedules, segment ownership including empty-output segments, independent hash domains, checkpoint
manifest order, task transitions, cancellation semantics and machine-readable JSON contracts.

M5A-02 adds fourteen real-checkpoint tests covering atomicity, exact same-environment continuation,
callback restoration, cross-checkpoint escape persistence, corruption, compatibility rejection and the
separation between immutable hashes and execution policy.

M5A-03/04 adds sixteen archive tests covering no-overwrite initialization, lock exclusivity/token
ownership, ordered hash chaining, exact boundary ownership, failures around both atomic commits,
checkpoint-chain continuity, idempotent recovery, artifact corruption, plan-external directories, orphan
reporting, resolved-contract tampering and lock tampering.

M5A-05/06 adds real-runner tests for uninterrupted versus binary-resumed equality, segment-range reading,
terminal collision semantics, a process interruption after an in-segment checkpoint, checkpoint retention
and the start/verify/inspect CLI. The interruption test verifies exact samples against a clean reference.

## Known limits and next PR

M5A does not auto-delete or steal a stale writer lock; operator-audited stale-lock recovery remains future
work. There is no asynchronous worker/control API, so no running-process pause command is exposed. A wall
quota safely pauses only after completing the current physical segment. Cross-version/platform checkpoint
portability is not promised. Collision continuation, tides, relativity, stellar evolution and weather are
outside this model. M3/M4/VIS consumers can use the streaming adapter in subsequent integration work;
their existing M2 archive paths and all prior commands remain unchanged.
