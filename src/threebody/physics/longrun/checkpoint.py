from __future__ import annotations

import hashlib
import math
import os
import platform
import shutil
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from threebody import __version__
from threebody.common.constants import G
from threebody.common.json import sha256_json, write_json_atomic
from threebody.errors import ConfigError
from threebody.physics.longrun.clock import PhysicalClock
from threebody.physics.longrun.models import (
    CheckpointManifest,
    ContractHashes,
    DiagnosticTrackerCheckpointState,
    EscapeTimerState,
    LongRunSettings,
    ObserverCheckpointState,
    RuntimeCheckpointState,
)
from threebody.physics.nbody.diagnostics import DiagnosticTracker
from threebody.physics.nbody.events import EventObserver
from threebody.physics.nbody.models import IntegrationSettings
from threebody.physics.nbody.rebound_ias15 import CollisionCapture
from threebody.world.models import WorldInitialState

CANONICAL_BODY_IDS = ("star-1", "star-2", "star-3", "planet-1")


@dataclass(frozen=True)
class RestoredCheckpoint:
    simulation: Any
    observer: EventObserver
    diagnostics: DiagnosticTracker
    collision_capture: CollisionCapture
    manifest: CheckpointManifest


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ConfigError(f"cannot hash checkpoint artifact {path}: {exc}") from exc
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    try:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    except OSError as exc:
        raise ConfigError(f"cannot fsync checkpoint artifact {path}: {exc}") from exc


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ConfigError(f"cannot fsync checkpoint directory {path}: {exc}") from exc


def _integration_settings(settings: LongRunSettings) -> IntegrationSettings:
    return IntegrationSettings(
        start_time_s=settings.time.start_time_s,
        duration_s=settings.time.output_interval_s,
        output_interval_s=settings.time.output_interval_s,
        epsilon=settings.epsilon,
        collision=settings.collision,
        encounter=settings.encounter,
        escape=settings.escape,
        require_positive_radius=settings.require_positive_radius,
        max_samples=2,
        max_wall_seconds=settings.resources.max_wall_seconds_per_invocation,
        softening_m=settings.softening_m,
    )


def capture_runtime_state(
    observer: EventObserver,
    diagnostics: DiagnosticTracker,
    collision_capture: CollisionCapture,
) -> RuntimeCheckpointState:
    if observer.timed_out or observer.numerical_error is not None:
        raise ConfigError("cannot checkpoint an observer in a stopped/error state")
    observer_state = ObserverCheckpointState(
        events=tuple(observer.events),
        minimum_observed_pair_distances_m=dict(
            observer.minimum_observed_pair_distances_m
        ),
        encounter_recorded=tuple(sorted(observer._encounter_recorded)),
        escape_recorded=tuple(sorted(observer._escape_recorded)),
        escape_timers=tuple(
            EscapeTimerState(body_index=index, started_s=started)
            for index, started in sorted(observer._escape_started_s.items())
        ),
        collision_particle_indices=collision_capture.particle_indices,
        collision_time_s=collision_capture.t_s,
    )
    diagnostic_state = DiagnosticTrackerCheckpointState(
        initial=diagnostics.initial,
        latest=diagnostics._latest,
        maximum_scaled_energy_error=diagnostics.maximum_scaled_energy_error,
        maximum_traditional_relative_energy_error=(
            diagnostics.maximum_traditional_relative_energy_error
        ),
        maximum_angular_momentum_relative_error=(
            diagnostics.maximum_angular_momentum_relative_error
        ),
        maximum_barycenter_position_drift_m=(
            diagnostics.maximum_barycenter_position_drift_m
        ),
        maximum_barycenter_velocity_drift_mps=(
            diagnostics.maximum_barycenter_velocity_drift_mps
        ),
    )
    return RuntimeCheckpointState(observer=observer_state, diagnostics=diagnostic_state)


def _restore_runtime_state(
    state: RuntimeCheckpointState,
    settings: LongRunSettings,
    initial_state: WorldInitialState,
) -> tuple[EventObserver, DiagnosticTracker, CollisionCapture]:
    integration_settings = _integration_settings(settings)
    observer = EventObserver(
        body_ids=tuple(body.id for body in initial_state.bodies),
        radii_m=tuple(body.radius_m for body in initial_state.bodies),
        settings=integration_settings,
    )
    observer.events = list(state.observer.events)
    observer.minimum_observed_pair_distances_m = dict(
        state.observer.minimum_observed_pair_distances_m
    )
    observer._encounter_recorded = set(state.observer.encounter_recorded)
    observer._escape_recorded = set(state.observer.escape_recorded)
    observer._escape_started_s = {
        timer.body_index: timer.started_s for timer in state.observer.escape_timers
    }
    tracker_state = state.diagnostics
    diagnostics = DiagnosticTracker(tracker_state.initial)
    diagnostics._latest = tracker_state.latest
    diagnostics.maximum_scaled_energy_error = tracker_state.maximum_scaled_energy_error
    diagnostics.maximum_traditional_relative_energy_error = (
        tracker_state.maximum_traditional_relative_energy_error
    )
    diagnostics.maximum_angular_momentum_relative_error = (
        tracker_state.maximum_angular_momentum_relative_error
    )
    diagnostics.maximum_barycenter_position_drift_m = (
        tracker_state.maximum_barycenter_position_drift_m
    )
    diagnostics.maximum_barycenter_velocity_drift_mps = (
        tracker_state.maximum_barycenter_velocity_drift_mps
    )
    collision_capture = CollisionCapture(
        particle_indices=state.observer.collision_particle_indices,
        t_s=state.observer.collision_time_s,
    )
    return observer, diagnostics, collision_capture


def _validate_simulation(
    simulation: Any,
    manifest: CheckpointManifest,
    settings: LongRunSettings,
    initial_state: WorldInitialState,
) -> None:
    if float(simulation.t) != manifest.simulation_time_s:
        raise ConfigError("checkpoint REBOUND time does not match manifest")
    if float(simulation.G) != G or manifest.gravitational_constant_si != G:
        raise ConfigError("checkpoint gravitational constant does not match SI contract")
    if simulation.integrator != "ias15":
        raise ConfigError("checkpoint integrator is not IAS15")
    if float(simulation.ri_ias15.epsilon) != settings.epsilon:
        raise ConfigError("checkpoint IAS15 epsilon does not match configuration")
    if len(simulation.particles) != 4:
        raise ConfigError("checkpoint does not contain exactly four bodies")
    try:
        import rebound
    except ImportError as exc:
        raise ConfigError("REBOUND is unavailable while validating checkpoint") from exc
    expected_hashes = [int(rebound.hash(body_id).value) for body_id in CANONICAL_BODY_IDS]
    observed_hashes = [int(particle.hash.value) for particle in simulation.particles]
    if observed_hashes != expected_hashes:
        raise ConfigError("checkpoint particle hash order is not canonical")
    for particle, body in zip(simulation.particles, initial_state.bodies, strict=True):
        if particle.m != body.mass_kg or particle.r != body.radius_m:
            raise ConfigError("checkpoint particle mass/radius differs from initial contract")


def save_checkpoint(
    simulation: Any,
    destination: str | Path,
    *,
    settings: LongRunSettings,
    hashes: ContractHashes,
    initial_state: WorldInitialState,
    initial_state_sha256: str,
    observer: EventObserver,
    diagnostics: DiagnosticTracker,
    collision_capture: CollisionCapture,
    run_id: str,
    checkpoint_index: int,
    segment_index: int,
    next_output_index: int,
) -> CheckpointManifest:
    target = Path(destination)
    if target.exists():
        raise ConfigError(f"checkpoint destination already exists: {target}")
    if not PhysicalClock(settings).is_safe_boundary(float(simulation.t)):
        raise ConfigError("checkpoint time is not a deterministic output/segment boundary")
    if sha256_json(initial_state) != initial_state_sha256:
        raise ConfigError("provided initial-state hash does not match initial state")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=str(target.parent))
    )
    binary_path = temporary / "rebound.bin"
    state_path = temporary / "observer_state.json"
    manifest_path = temporary / "checkpoint_manifest.json"
    try:
        simulation.save_to_file(str(binary_path), delete_file=True)
        _fsync_file(binary_path)
        binary_bytes = binary_path.stat().st_size
        if binary_bytes > settings.resources.max_checkpoint_bytes:
            raise ConfigError(
                f"checkpoint size {binary_bytes} exceeds configured byte limit"
            )
        runtime_state = capture_runtime_state(observer, diagnostics, collision_capture)
        write_json_atomic(state_path, runtime_state)
        _fsync_file(state_path)
        try:
            import rebound
        except ImportError as exc:
            raise ConfigError("REBOUND is unavailable while writing checkpoint") from exc
        manifest = CheckpointManifest(
            run_id=run_id,
            checkpoint_index=checkpoint_index,
            simulation_time_s=float(simulation.t),
            segment_index=segment_index,
            next_output_index=next_output_index,
            hashes=hashes,
            initial_state_sha256=initial_state_sha256,
            rebound_binary_sha256=_sha256_file(binary_path),
            observer_state_sha256=sha256_json(runtime_state),
            rebound_binary_bytes=binary_bytes,
            rebound_version=rebound.__version__,
            project_version=__version__,
            python_version=platform.python_version(),
            platform=platform.platform(),
            body_ids=tuple(body.id for body in initial_state.bodies),
            gravitational_constant_si=G,
            epsilon=settings.epsilon,
        )
        _validate_simulation(simulation, manifest, settings, initial_state)
        write_json_atomic(manifest_path, manifest)
        _fsync_file(manifest_path)
        _fsync_directory(temporary)
        os.replace(temporary, target)
        _fsync_directory(target.parent)
        return manifest
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def load_checkpoint(
    source: str | Path,
    *,
    settings: LongRunSettings,
    expected_hashes: ContractHashes,
    initial_state: WorldInitialState,
    initial_state_sha256: str,
) -> RestoredCheckpoint:
    directory = Path(source)
    try:
        manifest = CheckpointManifest.model_validate_json(
            (directory / "checkpoint_manifest.json").read_text(encoding="utf-8"),
            strict=True,
        )
        runtime_state = RuntimeCheckpointState.model_validate_json(
            (directory / "observer_state.json").read_text(encoding="utf-8"),
            strict=True,
        )
    except OSError as exc:
        raise ConfigError(f"cannot read checkpoint {directory}: {exc}") from exc
    except ValidationError as exc:
        raise ConfigError(f"invalid checkpoint metadata in {directory}: {exc}") from exc
    if manifest.hashes != expected_hashes:
        raise ConfigError("checkpoint contract hashes do not match requested run")
    if sha256_json(initial_state) != initial_state_sha256:
        raise ConfigError("provided initial-state hash does not match initial state")
    if manifest.initial_state_sha256 != initial_state_sha256:
        raise ConfigError("checkpoint initial-state hash does not match requested run")
    binary_path = directory / "rebound.bin"
    try:
        binary_bytes = binary_path.stat().st_size
    except OSError as exc:
        raise ConfigError(f"cannot stat checkpoint binary {binary_path}: {exc}") from exc
    if binary_bytes != manifest.rebound_binary_bytes:
        raise ConfigError("checkpoint binary size does not match manifest")
    if _sha256_file(binary_path) != manifest.rebound_binary_sha256:
        raise ConfigError("checkpoint binary SHA-256 does not match manifest")
    if sha256_json(runtime_state) != manifest.observer_state_sha256:
        raise ConfigError("checkpoint observer-state SHA-256 does not match manifest")
    try:
        import rebound
    except ImportError as exc:
        raise ConfigError("REBOUND is required to restore a checkpoint") from exc
    if settings.resume.require_same_rebound_version:
        if manifest.rebound_version != rebound.__version__:
            raise ConfigError("checkpoint REBOUND version is incompatible")
    if manifest.project_version != __version__:
        raise ConfigError("checkpoint project version is incompatible")
    if settings.resume.require_same_platform and manifest.platform != platform.platform():
        raise ConfigError("checkpoint platform is incompatible")
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="You have to reset function pointers.*",
            category=RuntimeWarning,
        )
        try:
            simulation = rebound.Simulation(str(binary_path))
        except (OSError, RuntimeError, ValueError) as exc:
            raise ConfigError(f"REBOUND could not load checkpoint binary: {exc}") from exc
    _validate_simulation(simulation, manifest, settings, initial_state)
    if runtime_state.observer.collision_particle_indices is not None:
        raise ConfigError("terminal collision checkpoint cannot be resumed")
    observer, diagnostics, collision_capture = _restore_runtime_state(
        runtime_state, settings, initial_state
    )
    simulation.collision = "line"
    simulation.collision_resolve = collision_capture.resolve
    simulation.heartbeat = observer.heartbeat
    if not math.isclose(float(simulation.t), manifest.simulation_time_s, abs_tol=0.0):
        raise ConfigError("checkpoint time changed while restoring callbacks")
    return RestoredCheckpoint(
        simulation=simulation,
        observer=observer,
        diagnostics=diagnostics,
        collision_capture=collision_capture,
        manifest=manifest,
    )
