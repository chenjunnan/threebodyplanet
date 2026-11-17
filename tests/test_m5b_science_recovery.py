"""Regressions for old completed runs with no M3/M4 and persisted analysis failures."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

import pytest

from threebody.physics.longrun.config import load_long_run_settings
from threebody.physics.longrun.models import LongRunSettings
from threebody.physics.longrun.runner import start_long_run
from threebody.web import jobs as jobs_module
from threebody.web.jobs import LocalRunController
from threebody.web.pipeline import derive_run_science
from threebody.web.server import create_console_server
from threebody.world.generators import generate_initial_state


@pytest.fixture
def old_completed_run(tmp_path, resolved):
    config, digest = resolved
    initial = generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )
    original, _ = load_long_run_settings("configs/m5a_long_run.yaml")
    raw = original.model_dump(mode="json")
    raw["time"] = {
        "start_time_s": 0.0, "end_time_s": 60.0, "output_interval_s": 10.0,
    }
    raw["segmentation"] = {"simulated_duration_s": 20.0}
    raw["checkpoint"] = {
        "simulation_interval_s": 10.0,
        "wall_interval_s": None,
        "retain_count": 3,
    }
    raw["resources"]["max_samples"] = 10
    raw["resources"]["max_segments"] = 10
    settings = LongRunSettings.model_validate(raw, strict=True)
    run = tmp_path / "previously-completed"
    result = start_long_run(
        initial, settings, run, run_id="previously-completed",
    )
    assert result.manifest.task.status == "COMPLETED"
    assert not (tmp_path / "environments" / run.name).exists()
    assert not (tmp_path / "outcomes" / run.name).exists()
    return run


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


def _request(url, payload=None):
    req = urllib.request.Request(
        url,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={} if payload is None else {"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _until_finished(endpoint, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code, status = _request(endpoint)
        assert code == 200, status
        if status["analysis_status"] in {"COMPLETED", "PARTIAL", "FAILED"}:
            return status
        time.sleep(0.05)
    pytest.fail("legacy run analysis did not reach a terminal status")


def test_legacy_completed_run_can_rebuild_both_missing_science_archives(
    tmp_path, old_completed_run
):
    run_id = old_completed_run.name
    with _server(tmp_path) as base:
        code, before = _request(f"{base}/api/v1/jobs/{run_id}")
        assert code == 200
        assert before["phase"] == "COMPLETED"
        assert before["analysis_status"] == "NOT_STARTED"
        assert before["artifacts_present"] == {"thermal": False, "outcome": False}
        code, accepted = _request(
            f"{base}/api/v1/runs/{run_id}/analyze", {},
        )
        assert code == 202, accepted
        result = _until_finished(f"{base}/api/v1/jobs/{run_id}")
        assert result["analysis_status"] == "COMPLETED", result
        assert result["artifacts_present"] == {"thermal": True, "outcome": True}
        assert result["analysis"]["thermal"] == "COMPLETED"
        assert result["analysis"]["outcome"] == "COMPLETED"
        for kind in ("environment", "outcome"):
            code, data = _request(f"{base}/api/v1/runs/{run_id}/{kind}")
            assert code == 200, data
            assert data["world_id"] == result["physical"].get(
                "world_id", data["world_id"]
            )
    # The Web server and in-memory job controller are now gone; analysis
    # progress and results must remain visible without redoing physics.
    with _server(tmp_path) as base:
        code, recovered = _request(f"{base}/api/v1/jobs/{run_id}")
        assert code == 200
        assert recovered["analysis_status"] == "COMPLETED"
        assert recovered["artifacts_present"] == {"thermal": True, "outcome": True}


def test_analysis_error_survives_controller_restart(
    tmp_path, old_completed_run, monkeypatch
):
    def broken_derived_science(_root, _id):
        raise RuntimeError("injected C1 solver exception")

    monkeypatch.setattr(jobs_module, "derive_run_science", broken_derived_science)
    first = LocalRunController(tmp_path)
    try:
        first.analyze(old_completed_run.name)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = first.status(old_completed_run.name)
            if status["analysis_status"] == "FAILED":
                break
            time.sleep(.02)
        else:
            pytest.fail("failure was not reported")
        assert "injected C1 solver exception" in status["analysis_error"]
    finally:
        first.close()
    second = LocalRunController(tmp_path)
    try:
        recovered = second.status(old_completed_run.name)
        assert recovered["analysis_status"] == "FAILED"
        assert "injected C1 solver exception" in recovered["analysis_error"]
        assert recovered["physical"]["status"] == "COMPLETED"
    finally:
        second.close()


def test_m3_numerical_failure_keeps_valid_m4_dynamics(
    tmp_path, old_completed_run, monkeypatch
):
    from threebody.web import pipeline

    def broken_thermal(*args):
        raise ValueError("injected thermal solver domain error")

    monkeypatch.setattr(pipeline, "derive_long_run_environment", broken_thermal)
    result = derive_run_science(tmp_path, old_completed_run.name)
    assert result["thermal"].startswith("UNAVAILABLE: ValueError:")
    assert result["outcome"] == "COMPLETED"
    assert (tmp_path / "outcomes" / old_completed_run.name /
            "long_outcome_manifest.json").is_file()
    assert not (tmp_path / "environments" / old_completed_run.name).exists()
