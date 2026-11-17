from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from threebody.common.json import canonical_json_bytes
from threebody.errors import ConfigError
from threebody.physics.longrun.clock import PhysicalClock, build_segment_index
from threebody.physics.longrun.config import load_long_run_settings
from threebody.physics.longrun.hashes import compute_contract_hashes
from threebody.physics.longrun.models import (
    CheckpointManifest,
    LongRunManifest,
    LongRunSettings,
    RunTaskState,
)
from threebody.physics.longrun.state import TERMINAL_STATES, transition_task


@pytest.fixture
def settings():
    return load_long_run_settings("configs/m5a_long_run.yaml")[0]


def _small(settings, **time_updates):
    time = settings.time.model_copy(
        update={
            "start_time_s": 10.0,
            "end_time_s": 45.0,
            "output_interval_s": 10.0,
            **time_updates,
        }
    )
    segmentation = settings.segmentation.model_copy(
        update={"simulated_duration_s": 12.0}
    )
    checkpoint = settings.checkpoint.model_copy(
        update={"simulation_interval_s": 6.0}
    )
    return settings.model_copy(
        update={
            "time": time,
            "segmentation": segmentation,
            "checkpoint": checkpoint,
        }
    )


def test_example_config_is_strict_versioned_and_stable(settings):
    loaded, digest = load_long_run_settings("configs/m5a_long_run.yaml")
    assert loaded == settings
    assert loaded.schema_version == "0.6.0"
    assert len(digest) == 64
    raw = loaded.model_dump(mode="json")
    raw["unknown"] = True
    with pytest.raises(ValidationError, match="Extra inputs"):
        LongRunSettings.model_validate(raw, strict=True)


def test_physical_clock_uses_global_indices_and_includes_true_endpoint(settings):
    clock = PhysicalClock(_small(settings))
    assert clock.output_count == 5
    assert [clock.output_time(index) for index in range(clock.output_count)] == [
        10.0,
        20.0,
        30.0,
        40.0,
        45.0,
    ]
    with pytest.raises(IndexError):
        clock.output_time(5)


def test_decimal_schedule_does_not_accumulate_repeated_addition_error(settings):
    configured = _small(
        settings, start_time_s=0.1, end_time_s=1.0, output_interval_s=0.1
    )
    clock = PhysicalClock(configured)
    assert clock.output_count == 10
    assert clock.output_time(3) == 0.4
    assert clock.output_time(9) == 1.0


def test_segment_index_has_contiguous_coverage_and_unique_sample_ownership(settings):
    configured = _small(settings)
    clock = PhysicalClock(configured)
    segments = build_segment_index(configured)
    assert [(entry.t_start_s, entry.t_end_s) for entry in segments] == [
        (10.0, 22.0),
        (22.0, 34.0),
        (34.0, 45.0),
    ]
    assert segments[0].owns_start_sample
    assert not any(entry.owns_start_sample for entry in segments[1:])
    owned = []
    for entry in segments:
        if entry.first_output_index is not None:
            owned.extend(range(entry.first_output_index, entry.last_output_index + 1))
    assert owned == list(range(clock.output_count))
    assert len(owned) == len(set(owned))


def test_segments_may_own_no_output_without_inventing_a_sample(settings):
    time = settings.time.model_copy(
        update={"start_time_s": 0.0, "end_time_s": 10.0, "output_interval_s": 10.0}
    )
    segmentation = settings.segmentation.model_copy(
        update={"simulated_duration_s": 3.0}
    )
    checkpoint = settings.checkpoint.model_copy(
        update={"simulation_interval_s": 1.0}
    )
    configured = settings.model_copy(
        update={"time": time, "segmentation": segmentation, "checkpoint": checkpoint}
    )
    segments = build_segment_index(configured)
    assert segments[0].first_output_index == 0
    assert segments[1].first_output_index is None
    assert segments[2].first_output_index is None
    assert segments[3].first_output_index == 1


def test_resource_limits_reject_unbounded_sample_or_segment_plans(settings):
    resources = settings.resources.model_copy(
        update={"max_samples": 2, "max_segments": 1}
    )
    raw = settings.model_dump(mode="json")
    raw["resources"] = resources.model_dump(mode="json")
    with pytest.raises(ValidationError, match="requires .* samples"):
        LongRunSettings.model_validate(raw, strict=True)


def test_checkpoint_interval_cannot_exceed_deterministic_segment(settings):
    raw = settings.model_dump(mode="json")
    raw["checkpoint"]["simulation_interval_s"] = (
        raw["segmentation"]["simulated_duration_s"] + 1.0
    )
    with pytest.raises(ValidationError, match="must not exceed segment duration"):
        LongRunSettings.model_validate(raw, strict=True)


def test_physics_schedule_and_execution_hashes_have_separate_domains(settings):
    body_ids = ("star-1", "star-2", "star-3", "planet-1")
    base = compute_contract_hashes(
        settings, initial_state_sha256="a" * 64, body_ids=body_ids
    )
    resources = settings.resources.model_copy(
        update={"max_wall_seconds_per_invocation": 60.0}
    )
    execution_change = settings.model_copy(update={"resources": resources})
    changed = compute_contract_hashes(
        execution_change, initial_state_sha256="a" * 64, body_ids=body_ids
    )
    assert changed.physics_sha256 == base.physics_sha256
    assert changed.schedule_sha256 == base.schedule_sha256
    assert changed.execution_policy_sha256 != base.execution_policy_sha256

    time = settings.time.model_copy(update={"output_interval_s": 43200.0})
    schedule_change = settings.model_copy(update={"time": time})
    changed = compute_contract_hashes(
        schedule_change, initial_state_sha256="a" * 64, body_ids=body_ids
    )
    assert changed.physics_sha256 == base.physics_sha256
    assert changed.schedule_sha256 != base.schedule_sha256


def test_task_state_machine_is_explicit_idempotent_and_terminal():
    task = RunTaskState(run_id="run-1", simulation_time_s=0.0)
    task = transition_task(task, "RUNNING")
    task = transition_task(task, "PAUSING")
    repeated = transition_task(task, "PAUSING")
    assert repeated is task
    task = transition_task(task, "PAUSED", reason_code="USER_REQUEST")
    task = transition_task(task, "RUNNING")
    task = transition_task(task, "COLLISION", reason_code="PHYSICAL_COLLISION")
    assert task.status in TERMINAL_STATES
    with pytest.raises(ConfigError, match="invalid long-run transition"):
        transition_task(task, "RUNNING")


def test_cancel_is_distinct_from_collision_and_solver_failure():
    pending = RunTaskState(run_id="run-2", simulation_time_s=0.0)
    cancelled = transition_task(pending, "CANCELLED", reason_code="USER_CANCELLED")
    assert cancelled.status == "CANCELLED"
    assert cancelled.reason_code == "USER_CANCELLED"
    assert cancelled.status not in {"COLLISION", "FAILED"}


def test_manifest_round_trip_is_finite_machine_readable_and_contiguous(settings):
    segments = build_segment_index(_small(settings))
    hashes = compute_contract_hashes(
        settings,
        initial_state_sha256="a" * 64,
        body_ids=("star-1", "star-2", "star-3", "planet-1"),
    )
    task = RunTaskState(run_id="run-3", simulation_time_s=10.0)
    manifest = LongRunManifest(
        run_id="run-3",
        world_id="candidate-000000",
        initial_state_sha256="a" * 64,
        hashes=hashes,
        task=task,
        segments=segments,
    )
    encoded = canonical_json_bytes(manifest)
    assert b"NaN" not in encoded and b"Infinity" not in encoded
    assert LongRunManifest.model_validate_json(encoded, strict=True) == manifest
    assert json.loads(encoded)["schema_version"] == "0.6.0"


def test_checkpoint_contract_requires_canonical_body_order(settings):
    hashes = compute_contract_hashes(
        settings,
        initial_state_sha256="a" * 64,
        body_ids=("star-1", "star-2", "star-3", "planet-1"),
    )
    values = {
        "run_id": "run-4",
        "checkpoint_index": 1,
        "simulation_time_s": 100.0,
        "segment_index": 0,
        "next_output_index": 2,
        "hashes": hashes,
        "initial_state_sha256": "a" * 64,
        "rebound_binary_sha256": "b" * 64,
        "observer_state_sha256": "c" * 64,
        "rebound_binary_bytes": 8684,
        "rebound_version": "4.6.0",
        "project_version": "0.6.0",
        "python_version": "3.11.18",
        "platform": "test-platform",
        "body_ids": ("star-1", "star-2", "star-3", "planet-1"),
        "gravitational_constant_si": 6.67430e-11,
        "epsilon": 1.0e-9,
    }
    assert CheckpointManifest(**values).body_ids[-1] == "planet-1"
    values["body_ids"] = ("planet-1", "star-1", "star-2", "star-3")
    with pytest.raises(ValidationError, match="canonical four-body order"):
        CheckpointManifest(**values)


def test_checked_in_json_schema_is_strict_and_versioned():
    schema = json.loads(Path("schemas/m5a_long_run.schema.json").read_text())
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"] == {"const": "0.6.0"}


def test_loader_rejects_non_mapping_and_unknown_fields(tmp_path):
    scalar = tmp_path / "scalar.yaml"
    scalar.write_text("42\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="must be a YAML mapping"):
        load_long_run_settings(scalar)

    unknown = tmp_path / "unknown.yaml"
    unknown.write_text("schema_version: '0.6.0'\nunknown: true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="validation failed"):
        load_long_run_settings(unknown)
