from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path

from threebody.cli import build_parser, execute
from threebody.common.json import write_json_atomic
from threebody.experiments.simulation import run_simulation_archive
from threebody.physics.nbody.rebound_ias15 import IntegrationExecutionError
from threebody.world.generators import generate_initial_state


def _write_inputs(tmp_path, resolved):
    config, digest = resolved
    state = generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )
    initial = tmp_path / "candidate.initial.json"
    write_json_atomic(initial, state)
    config_text = Path("configs/m2_ias15.yaml").read_text(encoding="utf-8")
    config_text = config_text.replace("duration_s: 31557600.0", "duration_s: 10000.0")
    config_text = config_text.replace("output_interval_s: 86400.0", "output_interval_s: 5000.0")
    simulation_config = tmp_path / "simulation.yaml"
    simulation_config.write_text(config_text, encoding="utf-8")
    return initial, simulation_config


def test_simulation_archive_is_complete_traceable_and_monotonic(tmp_path, resolved):
    initial, config = _write_inputs(tmp_path, resolved)
    output = tmp_path / "simulation"
    result = run_simulation_archive(initial, config, output)
    assert result.status == "COMPLETED"
    assert result.sample_count == 3
    assert result.t_final_s == 10000.0
    assert result.integrator_version
    assert result.initial_state_sha256
    assert result.settings_sha256
    expected = {
        "trajectory.jsonl",
        "events.json",
        "diagnostics.json",
        "resolved_simulation_config.json",
        "simulation_manifest.json",
    }
    assert {path.name for path in output.iterdir()} == expected
    samples = [json.loads(line) for line in (output / "trajectory.jsonl").read_text().splitlines()]
    assert [sample["t_s"] for sample in samples] == [0.0, 5000.0, 10000.0]
    assert all(
        second["t_s"] > first["t_s"] for first, second in pairwise(samples)
    )
    for path in output.iterdir():
        text = path.read_text(encoding="utf-8")
        assert "NaN" not in text and "Infinity" not in text
        if path.suffix == ".json":
            json.loads(text)
    diagnostic_archive = json.loads((output / "diagnostics.json").read_text())
    assert "minimum_observed_pair_distances_m" in diagnostic_archive
    assert diagnostic_archive["distance_semantics"] == "internal_step_endpoint_observation"


def test_world_simulate_cli_and_help_parser(tmp_path, resolved):
    initial, config = _write_inputs(tmp_path, resolved)
    output = tmp_path / "cli-simulation"
    parser = build_parser()
    args = parser.parse_args(
        [
            "world",
            "simulate",
            "--initial",
            str(initial),
            "--config",
            str(config),
            "--out-dir",
            str(output),
        ]
    )
    assert execute(args) == 0
    assert (output / "simulation_manifest.json").is_file()


def test_execution_failure_is_archived_as_failed_not_completed(tmp_path, resolved, monkeypatch):
    initial, config = _write_inputs(tmp_path, resolved)

    def fail(*_args, **_kwargs):
        raise IntegrationExecutionError("synthetic solver failure")

    monkeypatch.setattr("threebody.experiments.simulation.integrate_stream", fail)
    output = tmp_path / "failed-simulation"
    result = run_simulation_archive(initial, config, output)
    assert result.status == "FAILED"
    assert result.sample_count == 0
    assert "synthetic solver failure" in result.error
    manifest = json.loads((output / "simulation_manifest.json").read_text())
    assert manifest["status"] == "FAILED"


def test_real_ias15_wall_timeout_archives_stopped_and_cli_returns_one(
    tmp_path, resolved
):
    initial, config = _write_inputs(tmp_path, resolved)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "max_wall_seconds: 300.0", "max_wall_seconds: 1.0e-12"
        ),
        encoding="utf-8",
    )
    output = tmp_path / "stopped-simulation"
    result = run_simulation_archive(initial, config, output)
    assert result.status == "STOPPED"
    assert result.error == "max_wall_seconds reached"
    samples = [
        json.loads(line)
        for line in (output / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert samples
    assert result.sample_count == len(samples)
    assert result.t_final_s == samples[-1]["t_s"]
    assert result.t_final_s < 10_000.0
    manifest = json.loads((output / "simulation_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "STOPPED"
    assert manifest["t_final_s"] == samples[-1]["t_s"]

    cli_output = tmp_path / "stopped-cli-simulation"
    args = build_parser().parse_args(
        [
            "world",
            "simulate",
            "--initial",
            str(initial),
            "--config",
            str(config),
            "--out-dir",
            str(cli_output),
        ]
    )
    assert execute(args) == 1
    cli_manifest = json.loads(
        (cli_output / "simulation_manifest.json").read_text(encoding="utf-8")
    )
    assert cli_manifest["status"] == "STOPPED"

