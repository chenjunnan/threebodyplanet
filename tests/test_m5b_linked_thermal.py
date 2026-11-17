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
