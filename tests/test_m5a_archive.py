from __future__ import annotations

import json
from pathlib import Path

import pytest

from threebody.common.json import write_json_atomic
from threebody.errors import ConfigError
from threebody.physics.longrun.archive import (
    SingleWriterLock,
    commit_segment,
    initialize_run_archive,
    load_run_manifest,
    recover_run_archive,
    set_task_status,
    verify_run_archive,
)
from threebody.physics.longrun.models import LongRunSettings, WriterLockRecord
from threebody.physics.nbody.models import (
    BodyTrajectoryState,
    DiagnosticSnapshot,
    TrajectorySample,
)
from threebody.world.generators import generate_initial_state


def _settings() -> LongRunSettings:
    from threebody.physics.longrun.config import load_long_run_settings

    raw = load_long_run_settings("configs/m5a_long_run.yaml")[0].model_dump(mode="json")
    raw["time"] = {
        "start_time_s": 0.0,
        "end_time_s": 30.0,
        "output_interval_s": 10.0,
    }
    raw["segmentation"] = {"simulated_duration_s": 20.0}
    raw["checkpoint"] = {
        "simulation_interval_s": 10.0,
        "wall_interval_s": None,
        "retain_count": 3,
    }
    raw["resources"].update(
        {"max_segments": 10, "max_samples": 10, "max_wall_seconds_per_invocation": None}
    )
    return LongRunSettings.model_validate(raw, strict=True)


def _sample(t_s: float) -> TrajectorySample:
    bodies = tuple(
        BodyTrajectoryState(
            id=body_id,
            position_m=(float(index + t_s), float(index), 0.0),
            velocity_mps=(1.0, 0.0, 0.0),
        )
        for index, body_id in enumerate(
            ("star-1", "star-2", "star-3", "planet-1")
        )
    )
    diagnostics = DiagnosticSnapshot(
        t_s=t_s,
        kinetic_energy_j=1.0,
        potential_energy_j=-2.0,
        total_energy_j=-1.0,
        energy_scale_j=3.0,
        linear_momentum_kg_mps=(0.0, 0.0, 0.0),
        angular_momentum_kg_m2_s=(0.0, 0.0, 1.0),
        barycenter_position_m=(0.0, 0.0, 0.0),
        barycenter_velocity_mps=(0.0, 0.0, 0.0),
    )
    return TrajectorySample(t_s=t_s, bodies=bodies, diagnostics=diagnostics)


@pytest.fixture
def archive_case(tmp_path, resolved):
    config, digest = resolved
    initial = generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )
    directory = tmp_path / "long-run"
    manifest = initialize_run_archive(
        directory,
        run_id="run-archive-test",
        initial_state=initial,
        settings=_settings(),
    )
    return directory, manifest


def _commit_zero(directory: Path, token: str, failure_hook=None):
    return commit_segment(
        directory,
        0,
        (_sample(0.0), _sample(10.0), _sample(20.0)),
        (),
        {"summary": {"maximum_scaled_energy_error": 0.0}},
        lock_token=token,
        output_checkpoint_sha256="a" * 64,
        failure_hook=failure_hook,
    )


def test_initialize_archive_is_atomic_strict_and_non_overwriting(archive_case):
    directory, manifest = archive_case
    assert manifest.task.status == "PENDING"
    assert [entry.status for entry in manifest.segments] == ["PLANNED", "PLANNED"]
    assert {path.name for path in directory.iterdir()} == {
        "initial_state.json",
        "resolved_long_run_config.json",
        "long_run_manifest.json",
        "segments",
        "checkpoints",
        "locks",
    }
    with pytest.raises(ConfigError, match="already exists"):
        initialize_run_archive(
            directory,
            run_id=manifest.run_id,
            initial_state=None,
            settings=_settings(),
        )


def test_single_writer_lock_is_exclusive_and_not_auto_stolen(archive_case):
    directory, manifest = archive_case
    first = SingleWriterLock(directory, manifest.run_id).acquire()
    try:
        with pytest.raises(ConfigError, match="already exists"):
            SingleWriterLock(directory, manifest.run_id).acquire()
        assert (directory / "locks" / "writer.lock").is_file()
    finally:
        first.release()
    assert not (directory / "locks" / "writer.lock").exists()


def test_wrong_lock_token_cannot_mutate_task(archive_case):
    directory, manifest = archive_case
    with SingleWriterLock(directory, manifest.run_id):
        with pytest.raises(ConfigError, match="does not authorize"):
            set_task_status(directory, "RUNNING", lock_token="0" * 32)


def test_segments_commit_in_order_with_hash_chain_and_unique_boundaries(archive_case):
    directory, manifest = archive_case
    with SingleWriterLock(directory, manifest.run_id) as lock:
        set_task_status(directory, "RUNNING", lock_token=lock.token)
        first = _commit_zero(directory, lock.token)
        second = commit_segment(
            directory,
            1,
            (_sample(30.0),),
            (),
            {"summary": {}},
            lock_token=lock.token,
            input_checkpoint_sha256="a" * 64,
            output_checkpoint_sha256="b" * 64,
        )
    verified = verify_run_archive(directory)
    assert first.previous_segment_sha256 is None
    assert second.previous_segment_sha256 == verified.segments[0].segment_manifest_sha256
    assert verified.task.simulation_time_s == 30.0
    assert verified.task.segment_index == 2
    assert verified.task.next_output_index == 4
    times = []
    for path in sorted((directory / "segments").glob("*/trajectory.jsonl")):
        times.extend(json.loads(line)["t_s"] for line in path.read_text().splitlines())
    assert times == [0.0, 10.0, 20.0, 30.0]


def test_segment_out_of_order_and_duplicate_boundary_are_rejected(archive_case):
    directory, manifest = archive_case
    with SingleWriterLock(directory, manifest.run_id) as lock:
        set_task_status(directory, "RUNNING", lock_token=lock.token)
        with pytest.raises(ConfigError, match="physical order"):
            commit_segment(
                directory,
                1,
                (_sample(30.0),),
                (),
                {},
                lock_token=lock.token,
                output_checkpoint_sha256="b" * 64,
            )
        _commit_zero(directory, lock.token)
        with pytest.raises(ConfigError, match="does not match owned time"):
            commit_segment(
                directory,
                1,
                (_sample(20.0),),
                (),
                {},
                lock_token=lock.token,
                input_checkpoint_sha256="a" * 64,
                output_checkpoint_sha256="b" * 64,
            )


def test_segment_checkpoint_chain_must_continue_predecessor(archive_case):
    directory, manifest = archive_case
    with SingleWriterLock(directory, manifest.run_id) as lock:
        set_task_status(directory, "RUNNING", lock_token=lock.token)
        _commit_zero(directory, lock.token)
        with pytest.raises(ConfigError, match="input checkpoint"):
            commit_segment(
                directory,
                1,
                (_sample(30.0),),
                (),
                {},
                lock_token=lock.token,
                input_checkpoint_sha256="c" * 64,
                output_checkpoint_sha256="b" * 64,
            )


def test_failure_before_rename_never_commits_segment(archive_case):
    directory, manifest = archive_case

    def fail(phase):
        if phase == "before_segment_rename":
            raise OSError("injected before rename")

    with SingleWriterLock(directory, manifest.run_id) as lock:
        set_task_status(directory, "RUNNING", lock_token=lock.token)
        with pytest.raises(OSError, match="injected"):
            _commit_zero(directory, lock.token, fail)
    assert not (directory / "segments" / "00000000").exists()
    assert not list((directory / "segments").glob(".*.tmp-*"))
    assert load_run_manifest(directory).segments[0].status == "PLANNED"


def test_failure_after_rename_is_idempotently_registered_by_recovery(archive_case):
    directory, manifest = archive_case

    def fail(phase):
        if phase == "after_segment_rename":
            raise OSError("injected after rename")

    with SingleWriterLock(directory, manifest.run_id) as lock:
        set_task_status(directory, "RUNNING", lock_token=lock.token)
        with pytest.raises(OSError, match="injected"):
            _commit_zero(directory, lock.token, fail)
        assert (directory / "segments" / "00000000").is_dir()
        assert load_run_manifest(directory).segments[0].status == "PLANNED"
        recovered, orphans = recover_run_archive(directory, lock_token=lock.token)
        assert recovered.segments[0].status == "COMMITTED"
        assert not orphans
        repeated, _ = recover_run_archive(directory, lock_token=lock.token)
        assert repeated == recovered


def test_failure_after_global_manifest_needs_no_duplicate_replay(archive_case):
    directory, manifest = archive_case

    def fail(phase):
        if phase == "after_global_manifest":
            raise OSError("injected after manifest")

    with SingleWriterLock(directory, manifest.run_id) as lock:
        set_task_status(directory, "RUNNING", lock_token=lock.token)
        with pytest.raises(OSError, match="injected"):
            _commit_zero(directory, lock.token, fail)
        before = load_run_manifest(directory)
        recovered, _ = recover_run_archive(directory, lock_token=lock.token)
        assert recovered == before
    assert verify_run_archive(directory).segments[0].status == "COMMITTED"


def test_corrupt_committed_artifact_breaks_verification(archive_case):
    directory, manifest = archive_case
    with SingleWriterLock(directory, manifest.run_id) as lock:
        set_task_status(directory, "RUNNING", lock_token=lock.token)
        _commit_zero(directory, lock.token)
    trajectory = directory / "segments" / "00000000" / "trajectory.jsonl"
    trajectory.write_bytes(trajectory.read_bytes() + b"\n")
    with pytest.raises(ConfigError, match="trajectory SHA-256"):
        verify_run_archive(directory)


def test_durable_segment_outside_plan_is_rejected(archive_case):
    directory, _ = archive_case
    (directory / "segments" / "00000002").mkdir()
    with pytest.raises(ConfigError, match="outside the immutable plan"):
        verify_run_archive(directory)


def test_unexpected_durable_segment_artifact_is_rejected(archive_case):
    directory, _ = archive_case
    (directory / "segments" / "manually-copied").mkdir()
    with pytest.raises(ConfigError, match="unexpected durable segment artifact"):
        verify_run_archive(directory)


def test_orphan_temporary_is_reported_and_not_deleted_automatically(archive_case):
    directory, manifest = archive_case
    orphan = directory / "segments" / ".00000000.tmp-orphan"
    orphan.mkdir()
    with SingleWriterLock(directory, manifest.run_id) as lock:
        recovered, orphans = recover_run_archive(directory, lock_token=lock.token)
        assert recovered == manifest
        assert orphans == (orphan.name,)
    assert orphan.is_dir()


def test_lock_tampering_prevents_automatic_release(archive_case):
    directory, manifest = archive_case
    lock = SingleWriterLock(directory, manifest.run_id).acquire()
    path = directory / "locks" / "writer.lock"
    observed = WriterLockRecord.model_validate_json(path.read_text(), strict=True)
    write_json_atomic(path, observed.model_copy(update={"token": "f" * 32}))
    with pytest.raises(ConfigError, match="ownership changed"):
        lock.release()
    assert path.exists()
