# P0-M5A development report

Date: 2026-11-17
Branch stack: `feature/p0-m5a-contracts` → `feature/p0-m5a-checkpoints` →
`feature/p0-m5a-segments-recovery`
Current scope: M5A-01 contracts, M5A-02 binary checkpoints and M5A-03/04 archive/recovery foundation

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

The M5A-01 PR intentionally contains no checkpoint files or resume controls. Its dependent M5A-02 PR adds
the programmatic binary checkpoint foundation described below, but still does not expose long-run CLI,
Web controls or background workers. The next stacked PR adds the segmented archive foundation, but still
does not expose a user-level pause/resume command. No UI may advertise pause/resume yet.

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

## Verification

Executed with the Python 3.11 Conda environment:

```text
pytest -q
........................................................................ [ 42%]
........................................................................ [ 84%]
...........................                                              [100%]
185 tests collected; exit code 0

ruff check .
All checks passed!

python -m compileall -q src tests
exit code 0
```

Fourteen M5A-01 tests cover strict loading, finite resource bounds, non-divisible and fractional physical
time schedules, segment ownership including empty-output segments, independent hash domains, checkpoint
manifest order, task transitions, cancellation semantics and machine-readable JSON contracts.

M5A-02 adds thirteen real-checkpoint tests covering atomicity, exact same-environment continuation,
callback restoration, cross-checkpoint escape persistence, corruption and compatibility rejection.

M5A-03/04 adds fourteen archive tests covering no-overwrite initialization, lock exclusivity/token
ownership, ordered hash chaining, exact boundary ownership, failures around both atomic commits,
checkpoint-chain continuity, idempotent recovery, artifact corruption, plan-external directories, orphan
reporting and lock tampering.

## Known limits and next PR

The next dependent PR must connect these foundations to a real segmented IAS15 runner, checkpoint
retention/indexing, safe pause/quota handling, logical trajectory reader and CLI. The present APIs are not
a complete pause/resume product; no automatic stale-lock deletion or collision continuation exists.
