from __future__ import annotations

import pytest
import yaml

import threebody.physics.longrun.runner as runner_module
from threebody.cli import build_parser, execute
from threebody.common.json import write_json_atomic
from threebody.physics.longrun.archive import load_run_manifest
from threebody.physics.longrun.config import load_long_run_settings
from threebody.physics.longrun.models import LongRunSettings
from threebody.physics.longrun.reader import SegmentedTrajectoryReader
from threebody.physics.longrun.runner import resume_long_run, start_long_run
from threebody.physics.nbody.models import EncounterSettings, EscapeSettings
from threebody.world.generators import generate_initial_state
from threebody.world.models import BodyKind, CelestialBody, WorldInitialState


def _settings(*, end_s: float = 40.0) -> LongRunSettings:
    raw = load_long_run_settings("configs/m5a_long_run.yaml")[0].model_dump(mode="json")
    raw["time"] = {
        "start_time_s": 0.0,
        "end_time_s": end_s,
        "output_interval_s": 10.0,
    }
    raw["segmentation"] = {"simulated_duration_s": 20.0}
    raw["checkpoint"] = {
        "simulation_interval_s": 10.0,
        "wall_interval_s": None,
        "retain_count": 3,
    }
    raw["resources"] = {
        "max_wall_seconds_per_invocation": None,
        "max_segments": 10,
        "max_samples": 20,
        "max_checkpoint_bytes": 1_000_000,
    }
    raw["encounter"] = EncounterSettings(enabled=False).model_dump(mode="json")
    raw["escape"] = EscapeSettings(
        enabled=False, boundary_radius_m=1.0e20
    ).model_dump(mode="json")
    return LongRunSettings.model_validate(raw, strict=True)


def _generated_state(resolved):
    config, digest = resolved
    return generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )


def _body(body_id, kind, mass, radius, x, vx):
    return CelestialBody(
        id=body_id,
        kind=kind,
        mass_kg=float(mass),
        radius_m=float(radius),
        position_m=(float(x), 0.0, 0.0),
        velocity_mps=(float(vx), 0.0, 0.0),
        physical_profile_id="m5_collision_test",
    )


def _collision_world() -> WorldInitialState:
    return WorldInitialState(
        world_id="candidate-000000",
        seed=0,
        candidate_index=0,
        generator_id="m5_collision_test",
        generator_version="0.1.0",
        config_sha256="1" * 64,
        bodies=(
            _body("star-1", BodyKind.STAR, 1.0e10, 1.0, -10.0, 1000.0),
            _body("star-2", BodyKind.STAR, 1.0e10, 1.0, 10.0, -1000.0),
            _body("star-3", BodyKind.STAR, 1.0, 0.0, 1.0e7, 0.0),
            _body("planet-1", BodyKind.PLANET, 1.0, 0.0, -1.0e7, 0.0),
        ),
    )


def test_repeated_resume_matches_uninterrupted_segmented_run(tmp_path, resolved):
    initial = _generated_state(resolved)
    settings = _settings()
    uninterrupted_dir = tmp_path / "uninterrupted"
    resumed_dir = tmp_path / "resumed"

    uninterrupted = start_long_run(
        initial, settings, uninterrupted_dir, run_id="uninterrupted"
    )
    paused = start_long_run(
        initial,
        settings,
        resumed_dir,
        run_id="resumed",
        segment_limit=1,
    )
    assert paused.manifest.task.status == "PAUSED"
    resumed = resume_long_run(resumed_dir)

    assert uninterrupted.manifest.task.status == "COMPLETED"
    assert resumed.manifest.task.status == "COMPLETED"
    uninterrupted_samples = tuple(
        SegmentedTrajectoryReader(uninterrupted_dir).iter_samples()
    )
    resumed_samples = tuple(SegmentedTrajectoryReader(resumed_dir).iter_samples())
    assert resumed_samples == uninterrupted_samples
    assert [sample.t_s for sample in resumed_samples] == [0.0, 10.0, 20.0, 30.0, 40.0]
    assert tuple(SegmentedTrajectoryReader(resumed_dir).iter_events()) == tuple(
        SegmentedTrajectoryReader(uninterrupted_dir).iter_events()
    )


def test_reader_range_is_streamed_without_duplicate_segment_boundary(tmp_path, resolved):
    directory = tmp_path / "reader"
    start_long_run(
        _generated_state(resolved), _settings(), directory, run_id="reader-test"
    )
    reader = SegmentedTrajectoryReader(directory)
    assert [sample.t_s for sample in reader.iter_samples(start_s=10.0, end_s=30.0)] == [
        10.0,
        20.0,
        30.0,
    ]


def test_collision_commits_terminal_checkpoint_and_never_continues(tmp_path):
    directory = tmp_path / "collision"
    result = start_long_run(
        _collision_world(),
        _settings(end_s=20.0),
        directory,
        run_id="collision-test",
    )
    assert result.manifest.task.status == "COLLISION"
    assert 0.0 < result.manifest.task.simulation_time_s < 10.0
    assert result.manifest.segments[0].status == "TERMINAL"
    samples = tuple(SegmentedTrajectoryReader(directory).iter_samples())
    assert samples[-1].t_s == result.manifest.task.simulation_time_s
    events = tuple(SegmentedTrajectoryReader(directory).iter_events())
    collision = next(event for event in events if event.event_type == "COLLISION")
    assert collision.body_ids == ("star-1", "star-2")
    assert not (directory / "segments" / "00000001").exists()


def test_interrupted_running_invocation_replays_from_last_committed_checkpoint(
    tmp_path, resolved, monkeypatch
):
    directory = tmp_path / "interrupted"
    reference_dir = tmp_path / "interrupted-reference"
    initial = _generated_state(resolved)
    start_long_run(initial, _settings(), reference_dir, run_id="interrupted-reference")
    original_commit = runner_module.commit_segment
    attempts = 0

    def interrupt_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("injected process interruption after checkpoint")
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(runner_module, "commit_segment", interrupt_once)
    with pytest.raises(OSError, match="injected process interruption"):
        start_long_run(
            initial,
            _settings(),
            directory,
            run_id="interrupted-test",
        )
    assert len(tuple((directory / "checkpoints").glob("[0-9]" * 8))) == 2
    interrupted = load_run_manifest(directory)
    assert interrupted.task.status == "RUNNING"
    assert interrupted.task.simulation_time_s == 10.0
    assert interrupted.task.active_checkpoint_sha256 is not None

    recovered = resume_long_run(directory)
    assert recovered.manifest.task.status == "COMPLETED"
    assert [
        sample.t_s for sample in SegmentedTrajectoryReader(directory).iter_samples()
    ] == [0.0, 10.0, 20.0, 30.0, 40.0]
    assert len(tuple((directory / "checkpoints").glob("[0-9]" * 8))) == 3
    assert tuple(SegmentedTrajectoryReader(directory).iter_samples()) == tuple(
        SegmentedTrajectoryReader(reference_dir).iter_samples()
    )


def test_long_run_cli_start_verify_and_resume(tmp_path, resolved):
    initial_path = tmp_path / "initial.json"
    config_path = tmp_path / "long-run.yaml"
    run_dir = tmp_path / "cli-run"
    write_json_atomic(initial_path, _generated_state(resolved))
    config_path.write_text(
        yaml.safe_dump(_settings(end_s=20.0).model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )
    parser = build_parser()
    start_args = parser.parse_args(
        [
            "world",
            "simulate-long",
            "--initial",
            str(initial_path),
            "--config",
            str(config_path),
            "--out-dir",
            str(run_dir),
            "--run-id",
            "cli-test",
        ]
    )
    assert execute(start_args) == 0
    verify_args = parser.parse_args(
        ["world", "verify-long-run", "--run", str(run_dir)]
    )
    assert execute(verify_args) == 0
    inspect_args = parser.parse_args(
        ["world", "checkpoint", "inspect", "--run", str(run_dir)]
    )
    assert execute(inspect_args) == 0
