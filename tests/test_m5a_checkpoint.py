from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from threebody.common.json import sha256_json, write_json_atomic
from threebody.errors import ConfigError
from threebody.physics.longrun.checkpoint import load_checkpoint, save_checkpoint
from threebody.physics.longrun.config import load_long_run_settings
from threebody.physics.longrun.hashes import compute_contract_hashes
from threebody.physics.longrun.models import LongRunSettings
from threebody.physics.nbody.diagnostics import DiagnosticTracker
from threebody.physics.nbody.events import EventObserver
from threebody.physics.nbody.models import IntegrationSettings
from threebody.physics.nbody.rebound_ias15 import _build_simulation, _sample
from threebody.world.generators import generate_initial_state


def _long_settings() -> LongRunSettings:
    base = load_long_run_settings("configs/m5a_long_run.yaml")[0].model_dump(mode="json")
    base["time"] = {
        "start_time_s": 0.0,
        "end_time_s": 30.0,
        "output_interval_s": 10.0,
    }
    base["segmentation"] = {"simulated_duration_s": 20.0}
    base["checkpoint"] = {
        "simulation_interval_s": 10.0,
        "wall_interval_s": None,
        "retain_count": 3,
    }
    base["resources"] = {
        "max_wall_seconds_per_invocation": None,
        "max_segments": 10,
        "max_samples": 10,
        "max_checkpoint_bytes": 1_000_000,
    }
    base["encounter"] = {"enabled": False, "threshold_mode": "radius_multiple"}
    base["escape"] = {
        "enabled": True,
        "boundary_radius_m": 1.0e20,
        "require_outward_velocity": True,
        "require_positive_specific_energy": True,
        "minimum_duration_s": 10.0,
    }
    return LongRunSettings.model_validate(base, strict=True)


def _m2_settings(settings: LongRunSettings) -> IntegrationSettings:
    return IntegrationSettings(
        start_time_s=settings.time.start_time_s,
        duration_s=settings.time.end_time_s - settings.time.start_time_s,
        output_interval_s=settings.time.output_interval_s,
        epsilon=settings.epsilon,
        collision=settings.collision,
        encounter=settings.encounter,
        escape=settings.escape,
        max_samples=10,
    )


def _phase(simulation) -> np.ndarray:
    return np.asarray(
        [
            [
                particle.m,
                particle.r,
                particle.x,
                particle.y,
                particle.z,
                particle.vx,
                particle.vy,
                particle.vz,
            ]
            for particle in simulation.particles
        ]
    )


@pytest.fixture
def checkpoint_case(resolved):
    config, digest = resolved
    initial = generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )
    settings = _long_settings()
    initial_hash = sha256_json(initial)
    hashes = compute_contract_hashes(
        settings,
        initial_state_sha256=initial_hash,
        body_ids=("star-1", "star-2", "star-3", "planet-1"),
    )
    _, simulation, collision_capture = _build_simulation(initial, _m2_settings(settings))
    observer = EventObserver(
        body_ids=tuple(body.id for body in initial.bodies),
        radii_m=tuple(body.radius_m for body in initial.bodies),
        settings=_m2_settings(settings),
    )
    simulation.heartbeat = observer.heartbeat
    simulation.collision_resolve = collision_capture.resolve
    observer.observe(simulation, "initial_state")
    initial_sample = _sample(simulation, tuple(body.id for body in initial.bodies))
    tracker = DiagnosticTracker(initial_sample.diagnostics)
    simulation.integrate(10.0, exact_finish_time=1)
    sample = _sample(simulation, tuple(body.id for body in initial.bodies))
    tracker.update(sample.diagnostics)
    observer._encounter_recorded.add((0, 1))
    observer._escape_started_s[3] = 5.0
    return {
        "initial": initial,
        "initial_hash": initial_hash,
        "settings": settings,
        "hashes": hashes,
        "simulation": simulation,
        "observer": observer,
        "tracker": tracker,
        "collision_capture": collision_capture,
    }


def _save(case, directory: Path):
    return save_checkpoint(
        case["simulation"],
        directory,
        settings=case["settings"],
        hashes=case["hashes"],
        initial_state=case["initial"],
        initial_state_sha256=case["initial_hash"],
        observer=case["observer"],
        diagnostics=case["tracker"],
        collision_capture=case["collision_capture"],
        run_id="run-checkpoint-test",
        checkpoint_index=1,
        segment_index=0,
        next_output_index=2,
    )


def _load(case, directory: Path):
    return load_checkpoint(
        directory,
        settings=case["settings"],
        expected_hashes=case["hashes"],
        initial_state=case["initial"],
        initial_state_sha256=case["initial_hash"],
    )


def test_real_rebound_checkpoint_restores_binary_and_external_runtime_state(
    tmp_path, checkpoint_case
):
    directory = tmp_path / "checkpoint-000001"
    before = _phase(checkpoint_case["simulation"]).copy()
    manifest = _save(checkpoint_case, directory)
    restored = _load(checkpoint_case, directory)

    assert manifest.rebound_binary_bytes > 0
    assert np.array_equal(before, _phase(restored.simulation))
    assert restored.simulation.t == 10.0
    assert restored.simulation.G == checkpoint_case["simulation"].G
    assert restored.simulation.dt == checkpoint_case["simulation"].dt
    assert bool(restored.simulation._heartbeat)
    assert bool(restored.simulation._collision_resolve)
    assert restored.observer._encounter_recorded == {(0, 1)}
    assert restored.observer._escape_started_s == {3: 5.0}
    assert restored.observer.minimum_observed_pair_distances_m == (
        checkpoint_case["observer"].minimum_observed_pair_distances_m
    )
    assert restored.diagnostics.summary(conservation_applicable=True) == (
        checkpoint_case["tracker"].summary(conservation_applicable=True)
    )


def test_restored_and_uninterrupted_ias15_continuations_are_bitwise_equal(
    tmp_path, checkpoint_case
):
    directory = tmp_path / "checkpoint-000001"
    _save(checkpoint_case, directory)
    restored = _load(checkpoint_case, directory)
    continuous = checkpoint_case["simulation"]
    for target in (20.0, 30.0):
        continuous.integrate(target, exact_finish_time=1)
        restored.simulation.integrate(target, exact_finish_time=1)
        assert np.array_equal(_phase(continuous), _phase(restored.simulation))


def test_escape_persistence_crosses_checkpoint_and_emits_once(tmp_path, checkpoint_case):
    directory = tmp_path / "checkpoint-000001"
    _save(checkpoint_case, directory)
    restored = _load(checkpoint_case, directory)
    masses = np.asarray([body.mass_kg for body in checkpoint_case["initial"].bodies])
    positions = np.asarray(
        [[0.0, 0.0, 0.0], [1.0e9, 0.0, 0.0], [0.0, 1.0e9, 0.0], [2.0e20, 0.0, 0.0]]
    )
    velocities = np.asarray(
        [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [1.0e6, 0.0, 0.0]]
    )
    restored.observer._observe_escape(
        15.0, masses, positions, velocities, "restored_observer_test"
    )
    restored.observer._observe_escape(
        20.0, masses, positions, velocities, "restored_observer_test"
    )
    events = [
        event
        for event in restored.observer.events
        if event.event_type == "ESCAPE_CANDIDATE" and event.body_ids == ("planet-1",)
    ]
    assert len(events) == 1
    assert events[0].observed["condition_duration_s"] == 10.0


def test_checkpoint_is_atomic_and_refuses_overwrite(tmp_path, checkpoint_case):
    directory = tmp_path / "checkpoint-000001"
    _save(checkpoint_case, directory)
    assert {path.name for path in directory.iterdir()} == {
        "rebound.bin",
        "observer_state.json",
        "checkpoint_manifest.json",
    }
    assert not list(tmp_path.glob(".checkpoint-000001.tmp-*"))
    with pytest.raises(ConfigError, match="already exists"):
        _save(checkpoint_case, directory)


def test_checkpoint_rejects_non_deterministic_physical_time(tmp_path, checkpoint_case):
    checkpoint_case["simulation"].integrate(15.0, exact_finish_time=1)
    with pytest.raises(ConfigError, match="not a deterministic"):
        _save(checkpoint_case, tmp_path / "unsafe")


def test_corrupt_binary_is_rejected_before_rebound_load(tmp_path, checkpoint_case):
    directory = tmp_path / "checkpoint-000001"
    _save(checkpoint_case, directory)
    binary = directory / "rebound.bin"
    payload = bytearray(binary.read_bytes())
    payload[len(payload) // 2] ^= 0x01
    binary.write_bytes(payload)
    with pytest.raises(ConfigError, match="SHA-256"):
        _load(checkpoint_case, directory)


def test_corrupt_observer_state_is_rejected(tmp_path, checkpoint_case):
    directory = tmp_path / "checkpoint-000001"
    _save(checkpoint_case, directory)
    state_path = directory / "observer_state.json"
    raw = json.loads(state_path.read_text())
    raw["observer"]["escape_timers"][0]["started_s"] = 6.0
    write_json_atomic(state_path, raw)
    with pytest.raises(ConfigError, match="observer-state SHA-256"):
        _load(checkpoint_case, directory)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("rebound_version", "0.0.0", "REBOUND version"),
        ("project_version", "0.0.0", "project version"),
        ("platform", "different-platform", "platform"),
    ],
)
def test_incompatible_runtime_metadata_is_rejected(
    tmp_path, checkpoint_case, field, value, message
):
    directory = tmp_path / "checkpoint-000001"
    _save(checkpoint_case, directory)
    manifest_path = directory / "checkpoint_manifest.json"
    raw = json.loads(manifest_path.read_text())
    raw[field] = value
    write_json_atomic(manifest_path, raw)
    with pytest.raises(ConfigError, match=message):
        _load(checkpoint_case, directory)


def test_contract_or_initial_hash_mismatch_is_rejected(tmp_path, checkpoint_case):
    directory = tmp_path / "checkpoint-000001"
    _save(checkpoint_case, directory)
    wrong_hashes = checkpoint_case["hashes"].model_copy(
        update={"schedule_sha256": "f" * 64}
    )
    with pytest.raises(ConfigError, match="contract hashes"):
        load_checkpoint(
            directory,
            settings=checkpoint_case["settings"],
            expected_hashes=wrong_hashes,
            initial_state=checkpoint_case["initial"],
            initial_state_sha256=checkpoint_case["initial_hash"],
        )
    with pytest.raises(ConfigError, match="provided initial-state hash"):
        load_checkpoint(
            directory,
            settings=checkpoint_case["settings"],
            expected_hashes=checkpoint_case["hashes"],
            initial_state=checkpoint_case["initial"],
            initial_state_sha256="f" * 64,
        )


def test_terminal_collision_checkpoint_cannot_resume(tmp_path, checkpoint_case):
    checkpoint_case["collision_capture"].particle_indices = (0, 1)
    checkpoint_case["collision_capture"].t_s = 10.0
    directory = tmp_path / "checkpoint-000001"
    _save(checkpoint_case, directory)
    with pytest.raises(ConfigError, match="terminal collision"):
        _load(checkpoint_case, directory)


def test_observer_error_state_cannot_be_checkpointed(tmp_path, checkpoint_case):
    checkpoint_case["observer"].numerical_error = "failed"
    with pytest.raises(ConfigError, match="stopped/error"):
        _save(checkpoint_case, tmp_path / "invalid")
