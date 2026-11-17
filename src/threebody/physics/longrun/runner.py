from __future__ import annotations

import math
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from threebody.common.json import sha256_json
from threebody.errors import ConfigError
from threebody.physics.longrun.archive import (
    SingleWriterLock,
    commit_segment,
    initialize_run_archive,
    load_run_manifest,
    load_run_settings,
    recover_run_archive,
    set_active_checkpoint,
    set_task_status,
)
from threebody.physics.longrun.checkpoint import (
    RestoredCheckpoint,
    load_checkpoint,
    long_run_integration_settings,
    save_checkpoint,
)
from threebody.physics.longrun.clock import PhysicalClock
from threebody.physics.longrun.models import (
    CheckpointManifest,
    LongRunManifest,
    LongRunSettings,
    SegmentManifest,
)
from threebody.physics.nbody.diagnostics import DiagnosticTracker, NumericalSafetyError
from threebody.physics.nbody.events import EventObserver
from threebody.physics.nbody.models import TrajectorySample
from threebody.physics.nbody.rebound_ias15 import (
    CollisionCapture,
    IntegrationExecutionError,
    build_simulation,
    sample_simulation,
)
from threebody.world.models import WorldInitialState


@dataclass(frozen=True)
class LongRunInvocationResult:
    manifest: LongRunManifest
    segments_committed: int
    resumed: bool


@dataclass
class _Runtime:
    simulation: Any
    observer: EventObserver
    diagnostics: DiagnosticTracker
    collision_capture: CollisionCapture
    input_checkpoint_sha256: str | None
    pending_samples: tuple[TrajectorySample, ...] = ()
    segment_event_start_index: int = 0


def _read_initial_state(run_dir: Path) -> WorldInitialState:
    path = run_dir / "initial_state.json"
    try:
        return WorldInitialState.model_validate_json(
            path.read_text(encoding="utf-8"), strict=True
        )
    except OSError as exc:
        raise ConfigError(f"cannot read long-run initial state {path}: {exc}") from exc
    except ValidationError as exc:
        raise ConfigError(f"invalid long-run initial state {path}: {exc}") from exc


def _new_runtime(
    initial_state: WorldInitialState, settings: LongRunSettings
) -> tuple[_Runtime, TrajectorySample]:
    integration_settings = long_run_integration_settings(settings)
    rebound, simulation, collision_capture = build_simulation(
        initial_state, integration_settings
    )
    body_ids = tuple(body.id for body in initial_state.bodies)
    observer = EventObserver(
        body_ids=body_ids,
        radii_m=tuple(body.radius_m for body in initial_state.bodies),
        settings=integration_settings,
    )
    simulation.heartbeat = observer.heartbeat
    try:
        observer.observe(simulation, "initial_state")
        initial_sample = sample_simulation(simulation, body_ids)
    except (ArithmeticError, NumericalSafetyError, ValueError) as exc:
        raise IntegrationExecutionError(f"initial long-run observation failed: {exc}") from exc
    return (
        _Runtime(
            simulation=simulation,
            observer=observer,
            diagnostics=DiagnosticTracker(initial_sample.diagnostics),
            collision_capture=collision_capture,
            input_checkpoint_sha256=None,
        ),
        initial_sample,
    )


def _checkpoint_by_hash(run_dir: Path, expected_sha256: str) -> Path:
    matches: list[Path] = []
    checkpoint_paths = sorted(
        (
            path
            for path in (run_dir / "checkpoints").iterdir()
            if path.is_dir() and len(path.name) >= 8 and path.name.isdecimal()
        ),
        key=lambda path: int(path.name),
    )
    for path in checkpoint_paths:
        manifest_path = path / "checkpoint_manifest.json"
        try:
            manifest = CheckpointManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8"), strict=True
            )
        except (OSError, ValidationError) as exc:
            raise ConfigError(f"invalid checkpoint during archive scan {path}: {exc}") from exc
        if sha256_json(manifest) == expected_sha256:
            matches.append(path)
    if len(matches) != 1:
        raise ConfigError(
            f"expected exactly one checkpoint for hash {expected_sha256}; found {len(matches)}"
        )
    return matches[0]


def _restored_runtime(
    run_dir: Path,
    manifest: LongRunManifest,
    settings: LongRunSettings,
    initial_state: WorldInitialState,
) -> _Runtime:
    committed = [entry for entry in manifest.segments if entry.status == "COMMITTED"]
    active_hash = manifest.task.active_checkpoint_sha256
    if not committed and active_hash is None:
        runtime, _ = _new_runtime(initial_state, settings)
        return runtime
    latest = committed[-1] if committed else None
    input_checkpoint_hash = None
    if latest is not None:
        segment_manifest_path = (
            run_dir
            / "segments"
            / f"{latest.segment_index:08d}"
            / "segment_manifest.json"
        )
        try:
            segment_manifest = SegmentManifest.model_validate_json(
                segment_manifest_path.read_text(encoding="utf-8"), strict=True
            )
        except (OSError, ValidationError) as exc:
            raise ConfigError(f"invalid latest segment manifest: {exc}") from exc
        input_checkpoint_hash = segment_manifest.output_checkpoint_sha256
        if input_checkpoint_hash is None:
            raise ConfigError("latest committed segment has no resumable checkpoint")
    checkpoint_hash = active_hash or input_checkpoint_hash
    if checkpoint_hash is None:
        raise ConfigError("run manifest has no resumable checkpoint")
    restored: RestoredCheckpoint = load_checkpoint(
        _checkpoint_by_hash(run_dir, checkpoint_hash),
        settings=settings,
        expected_hashes=manifest.hashes,
        initial_state=initial_state,
        initial_state_sha256=manifest.initial_state_sha256,
    )
    expected_segment = (
        manifest.task.segment_index
        if active_hash is not None
        else latest.segment_index + 1
    )
    if restored.manifest.segment_index != expected_segment:
        raise ConfigError("latest checkpoint next-segment index is inconsistent")
    return _Runtime(
        simulation=restored.simulation,
        observer=restored.observer,
        diagnostics=restored.diagnostics,
        collision_capture=restored.collision_capture,
        input_checkpoint_sha256=input_checkpoint_hash,
        pending_samples=restored.pending_samples if active_hash is not None else (),
        segment_event_start_index=(
            restored.segment_event_start_index if active_hash is not None else 0
        ),
    )


def _checkpoint_hash(
    run_dir: Path,
    runtime: _Runtime,
    *,
    settings: LongRunSettings,
    manifest: LongRunManifest,
    initial_state: WorldInitialState,
    checkpoint_segment_index: int,
    next_output_index: int,
    pending_samples: tuple[TrajectorySample, ...],
    segment_event_start_index: int,
) -> str:
    existing_indices = [
        int(path.name)
        for path in (run_dir / "checkpoints").iterdir()
        if path.is_dir() and len(path.name) >= 8 and path.name.isdecimal()
    ]
    checkpoint_index = 0 if not existing_indices else max(existing_indices) + 1
    checkpoint = save_checkpoint(
        runtime.simulation,
        run_dir / "checkpoints" / f"{checkpoint_index:08d}",
        settings=settings,
        hashes=manifest.hashes,
        initial_state=initial_state,
        initial_state_sha256=manifest.initial_state_sha256,
        observer=runtime.observer,
        diagnostics=runtime.diagnostics,
        collision_capture=runtime.collision_capture,
        run_id=manifest.run_id,
        checkpoint_index=checkpoint_index,
        segment_index=checkpoint_segment_index,
        next_output_index=next_output_index,
        pending_samples=pending_samples,
        segment_event_start_index=segment_event_start_index,
    )
    return sha256_json(checkpoint)


def _segment_targets(
    clock: PhysicalClock, entry, settings: LongRunSettings
) -> tuple[tuple[float, bool, bool], ...]:
    output_times: list[float] = []
    if entry.first_output_index is not None:
        output_times = [
            clock.output_time(index)
            for index in range(entry.first_output_index, entry.last_output_index + 1)
        ]
    targets: dict[float, list[bool]] = {value: [True, False] for value in output_times}
    start = Decimal(str(settings.time.start_time_s))
    interval = Decimal(str(settings.checkpoint.simulation_interval_s))
    entry_start = Decimal(str(entry.t_start_s))
    entry_end = Decimal(str(entry.t_end_s))
    first_index = int(
        ((entry_start - start) / interval).to_integral_value(rounding=ROUND_FLOOR)
    ) + 1
    checkpoint_index = first_index
    while True:
        checkpoint_time = start + checkpoint_index * interval
        if checkpoint_time >= entry_end:
            break
        value = float(checkpoint_time)
        targets.setdefault(value, [False, False])[1] = True
        checkpoint_index += 1
    targets.setdefault(entry.t_end_s, [False, False])
    return tuple(
        (value, flags[0], flags[1]) for value, flags in sorted(targets.items())
    )


def _prune_checkpoints(run_dir: Path, retain_count: int) -> None:
    checkpoints = sorted(
        (
            path
            for path in (run_dir / "checkpoints").iterdir()
            if path.is_dir() and len(path.name) >= 8 and path.name.isdecimal()
        ),
        key=lambda path: int(path.name),
    )
    for obsolete in checkpoints[:-retain_count]:
        shutil.rmtree(obsolete)


def _run_segments(
    run_dir: Path,
    runtime: _Runtime,
    initial_sample: TrajectorySample | None,
    *,
    settings: LongRunSettings,
    initial_state: WorldInitialState,
    lock_token: str,
    segment_limit: int | None,
    monotonic: Callable[[], float],
    wall_limit_override: float | None,
    resumed: bool,
) -> LongRunInvocationResult:
    clock = PhysicalClock(settings)
    started_wall = monotonic()
    last_checkpoint_wall = started_wall
    committed_count = 0
    manifest = load_run_manifest(run_dir)
    body_ids = tuple(body.id for body in initial_state.bodies)
    for entry in manifest.segments:
        if entry.status != "PLANNED":
            continue
        if segment_limit is not None and committed_count >= segment_limit:
            break
        if runtime.pending_samples:
            if manifest.task.segment_index != entry.segment_index:
                raise ConfigError("pending checkpoint belongs to a different segment")
            samples = list(runtime.pending_samples)
            event_offset = runtime.segment_event_start_index
        else:
            samples = []
            event_offset = len(runtime.observer.events)
        if entry.segment_index == 0 and not samples:
            if initial_sample is None:
                if float(runtime.simulation.t) != settings.time.start_time_s:
                    raise ConfigError("segment zero cannot reconstruct its initial sample")
                initial_sample = sample_simulation(runtime.simulation, body_ids)
            samples.append(initial_sample)
        terminal_reason = None
        terminal_time = None
        for target, owns_output, creates_checkpoint in _segment_targets(
            clock, entry, settings
        ):
            if target <= float(runtime.simulation.t):
                continue
            try:
                runtime.simulation.integrate(target, exact_finish_time=1)
            except Exception as exc:
                raise IntegrationExecutionError(
                    f"REBOUND IAS15 failed at t={float(runtime.simulation.t)} s: {exc}"
                ) from exc
            if runtime.observer.numerical_error is not None:
                raise IntegrationExecutionError(runtime.observer.numerical_error)
            current_time = float(runtime.simulation.t)
            if runtime.collision_capture.particle_indices is not None:
                runtime.observer.observe(
                    runtime.simulation, "collision_callback_halt_state"
                )
                runtime.observer.collision_event(
                    runtime.simulation,
                    runtime.collision_capture.particle_indices,
                )
                terminal_reason = "COLLISION"
                terminal_time = current_time
                owns_output = True
            elif current_time != target:
                raise IntegrationExecutionError(
                    f"IAS15 did not finish at requested target {target}; got {current_time}"
                )
            sample = sample_simulation(runtime.simulation, body_ids)
            runtime.diagnostics.update(sample.diagnostics)
            if owns_output:
                samples.append(sample)
            if terminal_reason is not None:
                break
            wall_checkpoint_due = (
                settings.checkpoint.wall_interval_s is not None
                and monotonic() - last_checkpoint_wall
                >= settings.checkpoint.wall_interval_s
            )
            if (creates_checkpoint or wall_checkpoint_due) and current_time < entry.t_end_s:
                next_output = clock.first_output_after(current_time)
                active_hash = _checkpoint_hash(
                    run_dir,
                    runtime,
                    settings=settings,
                    manifest=manifest,
                    initial_state=initial_state,
                    checkpoint_segment_index=entry.segment_index,
                    next_output_index=next_output,
                    pending_samples=tuple(samples),
                    segment_event_start_index=event_offset,
                )
                set_active_checkpoint(
                    run_dir,
                    checkpoint_sha256=active_hash,
                    simulation_time_s=current_time,
                    segment_index=entry.segment_index,
                    next_output_index=next_output,
                    lock_token=lock_token,
                )
                last_checkpoint_wall = monotonic()
        if terminal_time is not None and terminal_time < settings.time.end_time_s:
            next_output = clock.first_output_after(terminal_time)
        else:
            next_output = (
                manifest.task.next_output_index
                if entry.last_output_index is None
                else entry.last_output_index + 1
            )
        output_checkpoint_hash = _checkpoint_hash(
            run_dir,
            runtime,
            settings=settings,
            manifest=manifest,
            initial_state=initial_state,
            checkpoint_segment_index=entry.segment_index + 1,
            next_output_index=next_output,
            pending_samples=(),
            segment_event_start_index=0,
        )
        summary = runtime.diagnostics.summary(
            conservation_applicable=terminal_reason != "COLLISION"
        )
        commit_segment(
            run_dir,
            entry.segment_index,
            tuple(samples),
            tuple(runtime.observer.events[event_offset:]),
            {
                "summary": summary.model_dump(mode="json"),
                "minimum_observed_pair_distances_m": dict(
                    runtime.observer.minimum_observed_pair_distances_m
                ),
                "distance_semantics": "internal_step_endpoint_observation",
            },
            lock_token=lock_token,
            input_checkpoint_sha256=runtime.input_checkpoint_sha256,
            output_checkpoint_sha256=output_checkpoint_hash,
            terminal_reason=terminal_reason,
            terminal_time_s=terminal_time,
        )
        runtime.input_checkpoint_sha256 = output_checkpoint_hash
        runtime.pending_samples = ()
        runtime.segment_event_start_index = 0
        committed_count += 1
        manifest = load_run_manifest(run_dir)
        _prune_checkpoints(run_dir, settings.checkpoint.retain_count)
        if terminal_reason is not None:
            return LongRunInvocationResult(manifest, committed_count, resumed)
        wall_limit = (
            wall_limit_override
            if wall_limit_override is not None
            else settings.resources.max_wall_seconds_per_invocation
        )
        if wall_limit is not None and monotonic() - started_wall >= wall_limit:
            break
    manifest = load_run_manifest(run_dir)
    if all(entry.status == "COMMITTED" for entry in manifest.segments):
        manifest = set_task_status(run_dir, "COMPLETED", lock_token=lock_token)
    else:
        manifest = set_task_status(run_dir, "PAUSING", lock_token=lock_token)
        manifest = set_task_status(run_dir, "PAUSED", lock_token=lock_token)
    return LongRunInvocationResult(manifest, committed_count, resumed)


def start_long_run(
    initial_state: WorldInitialState,
    settings: LongRunSettings,
    destination: str | Path,
    *,
    run_id: str,
    segment_limit: int | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    wall_limit_override: float | None = None,
) -> LongRunInvocationResult:
    if wall_limit_override is not None and (
        not math.isfinite(wall_limit_override) or wall_limit_override <= 0.0
    ):
        raise ConfigError("wall-limit override must be finite and positive")
    run_dir = Path(destination)
    manifest = initialize_run_archive(
        run_dir, run_id=run_id, initial_state=initial_state, settings=settings
    )
    runtime, initial_sample = _new_runtime(initial_state, settings)
    with SingleWriterLock(run_dir, manifest.run_id) as lock:
        set_task_status(run_dir, "RUNNING", lock_token=lock.token)
        return _run_segments(
            run_dir,
            runtime,
            initial_sample,
            settings=settings,
            initial_state=initial_state,
            lock_token=lock.token,
            segment_limit=segment_limit,
            monotonic=monotonic,
            wall_limit_override=wall_limit_override,
            resumed=False,
        )


def resume_long_run(
    run_dir: str | Path,
    *,
    segment_limit: int | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    max_wall_seconds: float | None = None,
) -> LongRunInvocationResult:
    if max_wall_seconds is not None and (
        not math.isfinite(max_wall_seconds) or max_wall_seconds <= 0.0
    ):
        raise ConfigError("max_wall_seconds must be finite and positive")
    directory = Path(run_dir)
    manifest = load_run_manifest(directory)
    if manifest.task.status not in {"PAUSED", "RUNNING"}:
        raise ConfigError("only a PAUSED run or interrupted RUNNING run may be resumed")
    settings = load_run_settings(directory)
    initial_state = _read_initial_state(directory)
    with SingleWriterLock(directory, manifest.run_id) as lock:
        manifest, _ = recover_run_archive(directory, lock_token=lock.token)
        runtime = _restored_runtime(directory, manifest, settings, initial_state)
        set_task_status(directory, "RUNNING", lock_token=lock.token)
        return _run_segments(
            directory,
            runtime,
            None,
            settings=settings,
            initial_state=initial_state,
            lock_token=lock.token,
            segment_limit=segment_limit,
            monotonic=monotonic,
            wall_limit_override=max_wall_seconds,
            resumed=True,
        )
