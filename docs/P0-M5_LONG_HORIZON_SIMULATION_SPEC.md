# P0-M5 — Long-horizon segmented simulation and recovery design

Status: technical design; no M5 implementation is included in this branch.
Date: 2026-11-17
Depends on: merged M2 archive semantics; M3/M4/VIS2 remain downstream read-only consumers.

## 1. Objective and scientific boundary

M5 extends the real REBOUND IAS15 M2 engine to simulations too long for one process lifetime. It adds
recoverable binary checkpoints, immutable integration segments, resource quotas and one authoritative
physical clock. It must not change Newtonian dynamics, silently reconstruct an orbit from elements,
restart from rounded JSON samples, or claim that a completed finite window proves stability.

M5 does not implement collision aftermath, stellar evolution, tides, relativity, distributed consensus,
weather, or a game-time clock. A collision remains terminal unless a later, separately versioned physical
model defines a new phase.

## 2. Time model: one physical clock, several explicitly nonphysical clocks

The authoritative physical time is `simulation_time_s = rebound.Simulation.t`, in SI seconds relative to
the initial state's `reference_epoch_s`. It is absolute over the complete run and never resets at a
segment boundary or process restart.

| name | meaning | may affect physics |
|---|---|---|
| `simulation_time_s` | authoritative REBOUND time | yes; integration target |
| `output_time_s` | deterministic points on the global output schedule | only by requesting an IAS15 target |
| `segment_boundary_s` | fixed physical boundaries in the run plan | yes; therefore hashed and immutable |
| `checkpoint_wall_interval_s` | operational snapshot cadence measured by monotonic wall clock | no physical meaning |
| `max_wall_seconds_per_invocation` | process resource quota | no physical meaning |
| UI playback/game time | presentation speed | never |

Output targets are derived from an integer index, not repeated floating-point addition:

```text
t_output(k) = min(t_start_s + k * output_interval_s, t_end_s)
```

The run state archives `next_output_index`; resumption must recompute the target from the immutable plan.
All M2, M3, M4 and VIS2 records continue to use the same absolute `t_s` basis.

## 3. Configuration and immutable hashes

M5 introduces a separate strict schema instead of changing M2 `0.2.0` semantics in place:

```yaml
schema_version: "0.6.0"
backend: rebound_ias15
time:
  start_time_s: 0.0
  end_time_s: 3.15576e10
  output_interval_s: 86400.0
segmentation:
  simulated_duration_s: 3.15576e7
checkpoint:
  simulation_interval_s: 2.592e6
  wall_interval_s: 300.0
resources:
  max_wall_seconds_per_invocation: 3600.0
  max_segments: 2000
  max_samples: 1000000
resume:
  require_same_rebound_version: true
  verify_all_committed_hashes: true
```

The real schema also contains the existing epsilon, collision, encounter, escape, radius and softening
settings. Unknown fields are rejected. Three hashes have different purposes:

- `physics_sha256`: initial-state hash, G/constants, backend, IAS15 epsilon, particle order and event
  physics. It is immutable.
- `schedule_sha256`: absolute start/end, output interval and physical segment boundaries. It is immutable
  because extra integration targets can change adaptive-step history.
- `execution_policy_sha256`: wall quotas, wall checkpoint cadence and local storage policy. A later
  invocation may change it, but each value is recorded and must not be described as a physics change.

Changing either immutable hash requires a new run ID. Resume never offers a force flag that bypasses
physics or schedule compatibility.

## 4. Checkpoint contract

REBOUND 4.6.0 provides `Simulation.save_to_file()` and `Simulationarchive`. The binary snapshot retains
simulation settings, constants, particle phase space and integrator state. M5 uses that format as the
solver checkpoint; it does not use trajectory JSON as restart state.

Each checkpoint is an atomically committed directory:

```text
checkpoints/00000042/
  rebound.bin
  checkpoint_manifest.json
  observer_state.json
```

`checkpoint_manifest.json` records schema/run/checkpoint IDs, absolute `simulation_time_s`, segment and
output indices, all immutable hashes, binary byte length and SHA-256, REBOUND/project/Python/platform
versions, particle IDs/order, `G`, integrator and IAS15 epsilon. `observer_state.json` contains state not
owned by REBOUND:

- collision terminal flag and causal participant indices, if any;
- close-encounter emitted keys and minimum observed pair distances;
- escape-candidate start times and already-emitted body IDs;
- diagnostic initial baseline, extrema and last valid snapshot;
- event sequence counter, next output index and last committed sample/event IDs;
- accumulated wall usage for reporting only.

Python callbacks are not assumed to survive binary serialization. Resume must reinstall the collision
resolver and heartbeat/event observer, then restore their explicit state before integrating.

Checkpoint creation writes a sibling temporary directory, fsyncs each file, hashes and rereads the
manifest, fsyncs the directory, and finally renames it atomically. A `LATEST` pointer is replaced only
after the checkpoint directory is durable. Recovery ignores orphan `.tmp-*` directories and chooses the
highest fully verified committed checkpoint, never merely the newest filename.

Binary portability is not silently promised. By default resume requires the recorded REBOUND version and
compatible platform representation. A migration tool may inspect or export a checkpoint, but a version
mismatch starts a new scientifically distinct run unless later validation establishes compatibility.

## 5. Immutable segments and global archive

Mutable append-only output is vulnerable to partial final lines and ambiguous replay. M5 therefore
commits each completed segment independently:

```text
run/
  long_run_manifest.json
  resolved_long_run_config.json
  segments/
    00000000/
      segment_manifest.json
      trajectory.jsonl
      events.json
      diagnostics.json
    00000001/
      ...
  checkpoints/
  invocations.jsonl
  locks/
```

Segment zero owns `[t_start, t_segment_end]`; later trajectory chunks own
`(t_segment_start, t_segment_end]`. Thus the logical concatenation is strictly increasing without
duplicate boundary samples. The checkpoint still contains the exact boundary state. A terminal collision
may end a segment before its planned boundary and becomes the global terminal time.

Segment manifests contain previous-segment hash, checkpoint input/output hashes, time coverage, sample
and event counts, status and per-artifact hashes. The chain prevents silent reordering or replacement.
Committed segments are never reopened. A resumed invocation creates the next segment or a new attempt for
the uncommitted segment.

Global status is one of `RUNNING`, `PAUSED`, `COMPLETED`, `COLLISION`, or `FAILED`. Reaching a wall quota
after a durable checkpoint produces `PAUSED`, not M2's terminal-looking `STOPPED`. Failure before a safe
checkpoint retains the previous committed state and records the invocation error without promoting
partial artifacts.

## 6. Resume algorithm

1. Acquire a single-writer lock with run ID, host, PID and creation time. Never delete an apparently
   active lock automatically; stale-lock recovery is an explicit audited CLI operation.
2. Validate the resolved config and all immutable hashes against the run manifest.
3. Verify the complete segment hash chain and select the latest durable checkpoint referenced by it.
4. Verify checkpoint artifact hashes before asking REBOUND to parse the binary.
5. Load the last simulation from `rebound.bin`; validate `t`, `G`, integrator, epsilon, four particle
   hashes/order, masses and radii against the manifest.
6. Reinstall Python collision and heartbeat callbacks. Restore observer/diagnostic/output scheduling
   state and assert that the next output time is strictly after the last committed sample.
7. Integrate only to deterministic output or segment targets. Write new data into a temporary segment.
8. On collision, quota, requested pause or segment completion, create and verify the required checkpoint,
   atomically commit the segment, then update the global manifest.

Resume is idempotent: repeating recovery after a crash cannot duplicate a sample or event. Event IDs are
run-scoped monotonic sequence IDs; observer state persists emitted close-encounter/escape keys. A collision
already committed is terminal and cannot be resumed as ordinary gravity.

## 7. Crash-consistency matrix

| interruption point | recovery behavior |
|---|---|
| while writing trajectory/event temp files | discard uncommitted temp segment |
| while writing checkpoint temp directory | discard temp checkpoint; use previous verified one |
| after checkpoint rename, before `LATEST` update | discover verified checkpoint by manifest scan |
| after segment rename, before global-manifest update | verify segment chain and finish idempotent index update |
| corrupt/truncated checkpoint | reject it and fall back only to a previously referenced verified checkpoint |
| hash/config/version mismatch | stop with configuration/recovery error; never integrate |
| wall quota reached | checkpoint and mark `PAUSED`; return a distinct nonfailure CLI result |

Failure-injection tests must exercise every row using process termination or injected I/O exceptions.

## 8. Diagnostics and event continuity

Global conservation diagnostics always compare against the original run baseline, not each segment's
first sample. Segment-local summaries may aid debugging but cannot replace global maxima/final errors.
All vector and energy calculations retain SI units and finite-value checks.

Collision callbacks remain causal REBOUND particle indices. Close-encounter minima retain the existing
internal-step endpoint limitation. Escape persistence timers continue across checkpoints; restarting them
at each segment would bias classification and is forbidden. No event is upgraded from candidate to proof
because it persists across a process restart.

## 9. Downstream compatibility

A `SegmentedTrajectoryReader` presents the existing logical `TrajectorySample` sequence while verifying
the segment hash chain and strict global time order. M3 can consume selected time ranges without loading
the whole run. M4 uses only observed coverage and terminal status. VIS1/VIS2 may decimate across segment
indexes while preserving global endpoints and events.

M5 does not require M3 to run concurrently with IAS15 in its first version. Offline thermal processing is
safer and reproducible. A later pipeline may process only durable completed segments, but it must key its
products to segment hashes and never read temporary files.

## 10. CLI design

```text
threebody world simulate-long --initial STATE --config M5.yaml --out-dir RUN
threebody world resume --run RUN [--max-wall-seconds N]
threebody world pause --run RUN
threebody world checkpoint inspect --run RUN [--id N]
threebody world verify-long-run --run RUN
```

Existing `threebody world simulate` and the M2 `0.2.0` archive remain unchanged. Output directories are
never overwritten. CLI results distinguish completed, collision-terminal, safely paused, configuration
error, corrupt recovery input and solver failure.

## 11. Implementation milestones

- **M5-01 — contracts and clock:** strict config/models, integer-index output schedule, run/segment/
  checkpoint manifests and backward-compatible reader protocol.
- **M5-02 — checkpoint capture/restore:** REBOUND binary archive, callback reinstallation, observer and
  diagnostic serialization, artifact hashing and compatibility guards.
- **M5-03 — segmented runner:** immutable chunk writer, atomic commit chain, pause/quota behavior and
  single-writer locking.
- **M5-04 — recovery:** idempotent resume, orphan discovery, corruption rejection and failure injection.
- **M5-05 — downstream adapter/CLI:** logical trajectory reader, range iteration, verification and CLI
  commands; M3/M4/VIS compatibility tests without changing their scientific models.
- **M5-06 — long-run validation:** uninterrupted-versus-resumed benchmarks, resource/memory tests,
  documentation and development report.

## 12. Acceptance tests

- At a checkpoint time, binary save/load reproduces `t`, particles, G, IAS15 settings and diagnostics.
- Under the same supported environment and fixed physical segment plan, uninterrupted and repeatedly
  resumed runs produce identical output/event schedules and scientifically equivalent phase space and
  conservation diagnostics. Bitwise equality is tested where REBOUND guarantees it but is not promised
  across library/platform versions.
- Escape persistence started before a checkpoint can complete after resume exactly once.
- A collision at or near a segment boundary records the callback participants once and never permits a
  postcollision segment.
- Every logical trajectory time is strictly increasing; final nonterminal time equals configured end.
- Changing wall quota does not change the physics/schedule hashes. Changing epsilon, event physics,
  output cadence or segment boundaries rejects resume.
- Corrupt binary, metadata, artifact hash, observer state, segment chain or `LATEST` pointer never yields
  a false `COMPLETED` result.
- Repeated crash/restart does not duplicate samples/events and memory stays bounded with at least
  100,000 logical samples.
- Existing M0–M4 and VIS1/VIS2 tests remain green; CI uses Python 3.11 and 3.12. A scheduled, non-CI
  benchmark covers a materially longer physical horizon and publishes runtime plus conservation data.

## 13. Open review decisions

Before implementation, reviewers must choose the supported REBOUND binary compatibility policy, maximum
checkpoint size/retention policy, and whether an operator-requested pause may occur only at the next
output target or at an internal heartbeat checkpoint. The choice affects reproducibility and must be
versioned; it must not be left as platform-dependent implicit behavior.
