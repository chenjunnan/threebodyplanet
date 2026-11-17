from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path

import pytest

from threebody.errors import ConfigError
from threebody.web.comparison import compare_runs
from threebody.web.server import create_console_server


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


def _request(url, value=None):
    payload = None if value is None else json.dumps(value).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={} if payload is None else {"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_real_web_world_auto_thermal_outcome_compare_export(tmp_path):
    preset = json.loads(
        files("threebody.web.static").joinpath("world_preset.json").read_text()
    )
    preset["experiment"]["seed"] = 20261116
    with _server(tmp_path) as base:
        code, batch = _request(
            base + "/api/v1/worldsets", {"config": preset, "count": 4}
        )
        assert code == 201, batch
        chosen = next(c for c in batch["candidates"] if c["can_integrate"])
        code, accepted = _request(base + "/api/v1/worlds/run", {
            "batch_id": batch["batch_id"],
            "candidate_index": chosen["index"], "run_id": "analysis-test",
            "duration_s": 60, "output_interval_s": 10,
        })
        assert code == 202, accepted
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            code, job = _request(base + "/api/v1/jobs/analysis-test")
            assert code == 200
            if job["analysis_status"] in {"COMPLETED", "PARTIAL", "FAILED"}:
                break
            time.sleep(0.05)
        else:
            pytest.fail("automatic science pipeline did not finish")
        assert job["phase"] == "COMPLETED", job
        assert job["analysis_status"] == "COMPLETED", job
        assert job["analysis"]["thermal"] == "COMPLETED"
        assert job["analysis"]["outcome"] == "COMPLETED"
        for endpoint in ("environment", "outcome"):
            code, data = _request(
                f"{base}/api/v1/runs/analysis-test/{endpoint}"
            )
            assert code == 200, data
            assert data["world_id"] == chosen["world_id"]
        for endpoint in ("compare", "report"):
            code, data = _request(
                f"{base}/api/v1/experiments/{endpoint}?runs=analysis-test"
            )
            assert code == 200, data
            assert data["runs"][0]["science_data_status"] == "M4_VERIFIED"
            assert data["runs"][0]["classification"] is not None
            assert data["comparison_sha256"]
            if endpoint == "report":
                assert data["report_type"] == "M5B_VERIFIED_SCIENCE_EXPORT"


def test_comparison_disallows_traversal_duplicate_ids_and_excess(tmp_path):
    with pytest.raises(ValueError):
        compare_runs(tmp_path, ["../bad"])
    with pytest.raises(ValueError):
        compare_runs(tmp_path, ["same", "same"])
    with pytest.raises(ValueError):
        compare_runs(tmp_path, [f"run-{i}" for i in range(9)])
    with pytest.raises(FileNotFoundError):
        compare_runs(tmp_path, ["missing"])


def test_comparison_refuses_tampered_physics(tmp_path):
    # Small archived sample constructed by the existing real IAS15 fixture.
    from threebody.config.loader import load_and_resolve
    from threebody.physics.longrun.config import load_long_run_settings
    from threebody.physics.longrun.models import LongRunSettings
    from threebody.physics.longrun.runner import start_long_run
    from threebody.world.generators import generate_initial_state

    config, digest = load_and_resolve(Path("configs/g21_bounded_worlds.yaml"))
    initial = generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )
    settings, _ = load_long_run_settings("configs/m5a_long_run.yaml")
    raw = settings.model_dump(mode="json")
    raw["time"] = {
        "start_time_s": 0.0, "end_time_s": 20.0, "output_interval_s": 10.0,
    }
    raw["segmentation"] = {"simulated_duration_s": 20.0}
    raw["checkpoint"] = {
        "simulation_interval_s": 10.0, "wall_interval_s": None, "retain_count": 3,
    }
    raw["resources"]["max_segments"] = 10
    raw["resources"]["max_samples"] = 10
    parsed = LongRunSettings.model_validate(raw, strict=True)
    directory = tmp_path / "safe"
    start_long_run(initial, parsed, directory, run_id="safe")
    report = compare_runs(tmp_path, ["safe"])
    assert report["runs"][0]["science_data_status"] == "TRAJECTORY_VERIFIED"
    sample_path = directory / "segments" / "00000000" / "trajectory.jsonl"
    sample_path.write_text(
        sample_path.read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )
    with pytest.raises((ConfigError, ValueError), match="SHA-256"):
        compare_runs(tmp_path, ["safe"])
