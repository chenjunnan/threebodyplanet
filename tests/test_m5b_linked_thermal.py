from __future__ import annotations

import json
import math
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager

import pytest
import yaml

from threebody.errors import ConfigError
from threebody.experiments.longrun_environment import derive_long_run_environment
from threebody.physics.longrun.config import load_long_run_settings
from threebody.physics.longrun.models import LongRunSettings
from threebody.physics.longrun.runner import start_long_run
from threebody.web.server import create_console_server
from threebody.web.thermal_science import read_long_environment
from threebody.world.generators import generate_initial_state


@pytest.fixture
def long_thermal_fixture(tmp_path, resolved):
    config, digest = resolved
    initial = generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )
    settings, _ = load_long_run_settings("configs/m5a_long_run.yaml")
    raw = settings.model_dump(mode="json")
    raw["time"] = {
        "start_time_s": 0.0,
        "end_time_s": 60.0,
        "output_interval_s": 10.0,
    }
    raw["segmentation"] = {"simulated_duration_s": 20.0}
    raw["checkpoint"] = {
        "simulation_interval_s": 10.0,
        "wall_interval_s": None,
        "retain_count": 3,
    }
    raw["resources"]["max_segments"] = 10
    raw["resources"]["max_samples"] = 10
    settings = LongRunSettings.model_validate(raw, strict=True)
    run = tmp_path / "test-world"
    result = start_long_run(initial, settings, run, run_id="test-world")
    assert result.manifest.task.status == "COMPLETED"
    thermal = yaml.safe_load(
        open("configs/m3_thermal.yaml", encoding="utf-8").read()
    )
    # Keep the original M3 physical parameters and sample cadence limit,
    # but a short 60-second interval is sufficient for cross-module validation.
    path = tmp_path / "thermal_config.yaml"
    path.write_text(yaml.safe_dump(thermal), encoding="utf-8")
    return run, path


def test_native_m5a_thermal_derivation_real_c0_c1_and_hashes(
    tmp_path, long_thermal_fixture
):
    run, config = long_thermal_fixture
    target = tmp_path / "environments" / run.name
    manifest = derive_long_run_environment(run, config, target)
    assert manifest["schema_version"] == "0.7.0"
    assert manifest["kind"] == "M5A_DERIVED_ENVIRONMENT"
    assert len(manifest["source"]["segments"]) == 3
    assert manifest["sample_count"] == 7
    assert manifest["solver"]["c0"] == "scipy.solve_ivp.RK45"
    assert manifest["solver"]["c1"] == "scipy.solve_ivp.BDF"
    assert manifest["energy_budget"]["c0"]["normalized_residual"] < 1e-4
    data = read_long_environment(run, target)
    assert data["run_id"] == run.name
    assert data["sample_count"] == 7
    assert [row["t_s"] for row in data["rows"]] == [
        0, 10, 20, 30, 40, 50, 60
    ]
    assert all(row["flux_w_m2"]["total"] > 0 for row in data["rows"])
    assert all(row["c0_temperature_k"] > 0 for row in data["rows"])
    assert len(data["rows"][0]["c1_temperature_k"]) == 36
    # Uniform initial C1 temperature is an explicit model setting, not a UI bug.
    initial_c1 = data["rows"][0]["c1_temperature_k"]
    assert max(initial_c1) == min(initial_c1) == 288.0
    # Latitude-dependent forcing must not be replaced by the single C0 flux.
    c1_rows = [
        json.loads(line)
        for line in (target / "c1_latitude_temperature.jsonl")
        .read_text(encoding="utf-8").splitlines()
    ]
    incoming = c1_rows[0]["zonal_incoming_w_m2"]
    assert max(incoming) > min(incoming)
    # A nonzero integration interval evolves nonuniform latitude temperatures.
    evolved = data["rows"][-1]["c1_temperature_k"]
    assert max(evolved) > min(evolved)
    assert max(evolved) - min(evolved) < 0.1  # 60 seconds and large heat capacity
    assert math.isclose(
        data["rows"][0]["flux_w_m2"]["total"],
        sum(data["rows"][0]["flux_w_m2"][x] for x in ("star-1", "star-2", "star-3")),
        rel_tol=1e-12,
    )
    sampled = read_long_environment(
        run, target, start_s=10.0, end_s=50.0, max_points=3
    )
    assert [row["t_s"] for row in sampled["rows"]] == [10, 30, 50]
    with pytest.raises(ConfigError, match="already exists"):
        derive_long_run_environment(run, config, target)


def test_thermal_source_chain_and_derived_data_tampering_rejected(
    tmp_path, long_thermal_fixture
):
    run, config = long_thermal_fixture
    target = tmp_path / "environments" / run.name
    derive_long_run_environment(run, config, target)
    path = target / "c0_temperature.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="SHA-256 mismatch"):
        read_long_environment(run, target)
    path.write_text(path.read_text(encoding="utf-8").rstrip("\n") + "\n",
                    encoding="utf-8")
    manifest = json.loads((target / "long_environment_manifest.json").read_text())
    manifest["source"]["segments"][0]["sha256"] = "f" * 64
    (target / "long_environment_manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ConfigError, match="provenance chain"):
        read_long_environment(run, target)


@contextmanager
def _server(root):
    server = create_console_server(root, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_http_environment_is_source_bound_and_missing_never_fabricated(
    tmp_path, long_thermal_fixture
):
    run, config = long_thermal_fixture
    with _server(tmp_path) as url:
        code, result = _get(f"{url}/api/v1/runs/{run.name}/environment")
        assert code == 404
        assert result["error"] == "run_not_found"
    target = tmp_path / "environments" / run.name
    derive_long_run_environment(run, config, target)
    with _server(tmp_path) as url:
        code, result = _get(
            f"{url}/api/v1/runs/{run.name}/environment"
            "?start_s=20&end_s=40&max_points=2"
        )
        assert code == 200, result
        assert [row["t_s"] for row in result["rows"]] == [20, 40]
        for query in (
            "?start_s=NaN", "?max_points=2001",
            "?start_s=40&end_s=20", "?bogus=1",
        ):
            code, _ = _get(
                f"{url}/api/v1/runs/{run.name}/environment{query}"
            )
            assert code == 422


@pytest.mark.parametrize(
    ("mode", "c0_expected", "c1_expected"),
    [("c0", True, False), ("c1", False, True)],
)
def test_thermal_modes_preserve_missing_data_semantics(
    tmp_path, long_thermal_fixture, mode, c0_expected, c1_expected
):
    run, config = long_thermal_fixture
    document = yaml.safe_load(config.read_text(encoding="utf-8"))
    document["thermal"]["mode"] = mode
    mode_config = tmp_path / f"{mode}.yaml"
    mode_config.write_text(yaml.safe_dump(document), encoding="utf-8")
    derived = tmp_path / "derived" / mode
    derive_long_run_environment(run, mode_config, derived)
    data = read_long_environment(run, derived)
    assert data["thermal_mode"] == mode
    assert all(
        (row["c0_temperature_k"] is not None) is c0_expected
        for row in data["rows"]
    )
    assert all(
        (row["c1_temperature_k"] is not None) is c1_expected
        for row in data["rows"]
    )


def test_m6_resumable_c0_c1_replays_and_matches_monolithic(
    tmp_path, long_thermal_fixture
):
    run, config = long_thermal_fixture
    legacy_path = tmp_path / "legacy-environment"
    legacy = derive_long_run_environment(run, config, legacy_path)
    checkpoints = tmp_path / "thermal_checkpoints" / run.name
    segmented_path = tmp_path / "segmented-environment"
    segmented = derive_long_run_environment(
        run, config, segmented_path,
        checkpoint_dir=checkpoints, chunk_output_samples=3,
    )
    assert segmented["processing"]["chunk_count"] == 3
    assert segmented["processing"]["computed_chunks"] == 3
    assert segmented["processing"]["reused_chunks"] == 0
    assert segmented["processing"]["c0_function_evaluations"] > 0
    assert segmented["processing"]["c1_function_evaluations"] > 0
    assert segmented["source"] == legacy["source"]
    old = read_long_environment(run, legacy_path)["rows"]
    new = read_long_environment(run, segmented_path)["rows"]
    assert [p["t_s"] for p in old] == [p["t_s"] for p in new]
    for a, b in zip(old, new, strict=True):
        assert a["c0_temperature_k"] == pytest.approx(
            b["c0_temperature_k"], abs=2e-4
        )
        assert a["c1_temperature_k"] == pytest.approx(
            b["c1_temperature_k"], abs=2e-4
        )
    assert segmented["energy_budget"]["c0"]["normalized_residual"] < 1e-4
    assert segmented["energy_budget"]["c1"]["normalized_residual"] < 1e-4

    second_path = tmp_path / "resumed-environment"
    resumed = derive_long_run_environment(
        run, config, second_path,
        checkpoint_dir=checkpoints, chunk_output_samples=3,
    )
    assert resumed["processing"]["reused_chunks"] == 3
    assert resumed["processing"]["computed_chunks"] == 0
    assert (segmented_path / "c1_latitude_temperature.jsonl").read_bytes() == (
        second_path / "c1_latitude_temperature.jsonl"
    ).read_bytes()


def test_m6_resume_after_last_chunk_missing_and_reject_corrupt_or_stale(
    tmp_path, long_thermal_fixture
):
    run, config = long_thermal_fixture
    checkpoints = tmp_path / "thermal-checkpoints"
    derive_long_run_environment(
        run, config, tmp_path / "first-pass",
        checkpoint_dir=checkpoints, chunk_output_samples=3,
    )
    (checkpoints / "00000002.json").unlink()
    result = derive_long_run_environment(
        run, config, tmp_path / "recovered",
        checkpoint_dir=checkpoints, chunk_output_samples=3,
    )
    assert result["processing"]["reused_chunks"] == 2
    assert result["processing"]["computed_chunks"] == 1

    target = checkpoints / "00000000.json"
    doc = json.loads(target.read_text())
    doc["payload"]["c0"]["samples"][0]["temperature_k"] = 100.0
    target.write_text(json.dumps(doc))
    with pytest.raises(ConfigError, match="digest mismatch"):
        derive_long_run_environment(
            run, config, tmp_path / "refuse-corruption",
            checkpoint_dir=checkpoints, chunk_output_samples=3,
        )
    assert not (tmp_path / "refuse-corruption").exists()


def test_m6_checkpoint_rejects_changed_thermal_parameters(
    tmp_path, long_thermal_fixture
):
    run, config = long_thermal_fixture
    checkpoints = tmp_path / "science-checkpoints"
    derive_long_run_environment(
        run, config, tmp_path / "original",
        checkpoint_dir=checkpoints, chunk_output_samples=3,
    )
    document = yaml.safe_load(config.read_text(encoding="utf-8"))
    document["thermal"]["bond_albedo"] = 0.5
    updated = tmp_path / "different.yaml"
    updated.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ConfigError, match="provenance/settings mismatch"):
        derive_long_run_environment(
            run, updated, tmp_path / "must-not-exist",
            checkpoint_dir=checkpoints, chunk_output_samples=3,
        )
