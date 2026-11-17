from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager

import pytest

from threebody.errors import ConfigError
from threebody.experiments.longrun_assessment import assess_long_run
from threebody.experiments.longrun_environment import derive_long_run_environment
from threebody.physics.longrun.config import load_long_run_settings
from threebody.physics.longrun.models import LongRunSettings
from threebody.physics.longrun.runner import start_long_run
from threebody.web.outcome_science import read_long_outcome
from threebody.web.server import create_console_server
from threebody.world.generators import generate_initial_state


@pytest.fixture
def long_world(tmp_path, resolved):
    config, digest = resolved
    initial = generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )
    settings, _ = load_long_run_settings("configs/m5a_long_run.yaml")
    raw = settings.model_dump(mode="json")
    raw["time"] = {
        "start_time_s": 0.0, "end_time_s": 60.0, "output_interval_s": 10.0,
    }
    raw["segmentation"] = {"simulated_duration_s": 20.0}
    raw["checkpoint"] = {
        "simulation_interval_s": 10.0, "wall_interval_s": None, "retain_count": 3,
    }
    raw["resources"]["max_segments"] = 10
    raw["resources"]["max_samples"] = 10
    settings = LongRunSettings.model_validate(raw, strict=True)
    run = tmp_path / "test-outcome"
    result = start_long_run(initial, settings, run, run_id="test-outcome")
    assert result.manifest.task.status == "COMPLETED"
    return run


@contextmanager
def _web(root):
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
        with urllib.request.urlopen(url, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_long_run_m4_without_thermal_is_explicitly_insufficient(tmp_path, long_world):
    outcome = tmp_path / "outcomes" / long_world.name
    manifest = assess_long_run(
        long_world, "configs/m4_world_outcomes.yaml", outcome,
    )
    assert manifest["source_status"] == "COMPLETED"
    assert manifest["environment_manifest_sha256"] is None
    observed = read_long_outcome(long_world, outcome, None)
    classification = observed["classification"]
    assert classification["dynamical_outcome"] == "COMPLETED_OBSERVATION_WINDOW"
    assert "DATA_INSUFFICIENT" in classification["environmental_outcomes"]
    with _web(tmp_path) as url:
        code, response = _get(f"{url}/api/v1/runs/{long_world.name}/outcome")
        assert code == 200, response
        assert response["source"]["thermal"] == "missing"
    file = outcome / "world_classification.json"
    file.write_text(file.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(Exception, match="SHA-256 mismatch"):
        read_long_outcome(long_world, outcome, None)


def test_long_run_m4_uses_actual_c0_c1_and_source_hash(tmp_path, long_world):
    environment = tmp_path / "environments" / long_world.name
    derive_long_run_environment(
        long_world, "configs/m3_thermal.yaml", environment,
    )
    outcome = tmp_path / "outcomes" / long_world.name
    manifest = assess_long_run(
        long_world, "configs/m4_world_outcomes.yaml", outcome,
        environment_dir=environment,
    )
    assert manifest["environment_manifest_sha256"] is not None
    observed = read_long_outcome(long_world, outcome, environment)
    classification = observed["classification"]
    assert "DATA_INSUFFICIENT" not in classification["environmental_outcomes"]
    assert observed["source"] == {"m5a": "verified", "thermal": "verified"}
    with _web(tmp_path) as url:
        code, response = _get(f"{url}/api/v1/runs/{long_world.name}/outcome")
        assert code == 200, response
        assert response["world_id"] == observed["world_id"]
    env_manifest_path = environment / "long_environment_manifest.json"
    data = json.loads(env_manifest_path.read_text(encoding="utf-8"))
    data["thermal_mode"] = "c1"
    env_manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError):
        read_long_outcome(long_world, outcome, environment)
