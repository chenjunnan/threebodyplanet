from __future__ import annotations

import json
import math
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager

import pytest

from threebody.physics.longrun.config import load_long_run_settings
from threebody.physics.longrun.models import LongRunSettings
from threebody.physics.longrun.runner import start_long_run
from threebody.web.science import query_events, query_trajectory
from threebody.web.server import create_console_server
from threebody.world.generators import generate_initial_state


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
    request = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@pytest.fixture
def run_dir(tmp_path, resolved):
    config, digest = resolved
    state = generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )
    original, _ = load_long_run_settings("configs/m5a_long_run.yaml")
    raw = original.model_dump(mode="json")
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
    run = tmp_path / "science-run"
    result = start_long_run(state, settings, run, run_id="science-run")
    assert result.manifest.task.status == "COMPLETED"
    return run


def test_range_sampling_preserves_existing_endpoints_and_actual_vectors(run_dir):
    all_points = query_trajectory(run_dir, start_s=None, end_s=None, max_points=20)
    assert all_points["sample_count"] == 7
    assert [p["t_s"] for p in all_points["samples"]] == [0, 10, 20, 30, 40, 50, 60]
    assert len(all_points["samples"][0]["positions_m"]) == 4
    sampled = query_trajectory(run_dir, start_s=10, end_s=50, max_points=3)
    assert sampled["sample_count"] == 5
    assert [p["t_s"] for p in sampled["samples"]] == [10, 30, 50]
    assert sampled["display_sampling"] == "uniform_record_index_no_interpolation"
    for point in sampled["samples"]:
        assert all(
            math.isfinite(v)
            for xyz in point["positions_m"].values()
            for v in xyz
        )


def test_range_is_empty_not_extrapolated_and_rejects_invalid_limits(run_dir):
    empty = query_trajectory(run_dir, start_s=100, end_s=200)
    assert empty["sample_count"] == 0
    assert empty["samples"] == []
    for kwargs in [
        {"start_s": 10, "end_s": 0},
        {"start_s": math.nan, "end_s": None},
        {"start_s": None, "end_s": math.inf},
    ]:
        with pytest.raises(ValueError):
            query_trajectory(run_dir, **kwargs)
    with pytest.raises(ValueError, match="max_points"):
        query_trajectory(run_dir, start_s=None, end_s=None, max_points=1)
    event_data = query_events(run_dir, start_s=None, end_s=None)
    assert event_data["events"] == []
    assert event_data["escape_semantics"] == "candidate_not_proven_permanent_escape"


def test_http_ranges_are_verified_bounded_and_expose_only_committed_science(run_dir):
    with _server(run_dir.parent) as url:
        status, full = _get(f"{url}/api/v1/runs/science-run/trajectory?max_points=2")
        assert status == 200
        assert full["sample_count"] == 7
        assert [p["t_s"] for p in full["samples"]] == [0, 60]
        status, part = _get(
            f"{url}/api/v1/runs/science-run/trajectory"
            "?start_s=20&end_s=40&max_points=10"
        )
        assert status == 200
        assert [p["t_s"] for p in part["samples"]] == [20, 30, 40]
        status, events = _get(f"{url}/api/v1/runs/science-run/events")
        assert status == 200
        assert events["events"] == []
        for suffix in [
            "?max_points=2001",
            "?start_s=NaN",
            "?start_s=20&start_s=40",
            "?unknown=1",
            "?start_s=40&end_s=20",
        ]:
            status, payload = _get(
                f"{url}/api/v1/runs/science-run/trajectory{suffix}"
            )
            assert status == 422, payload
        status, _ = _get(f"{url}/api/v1/runs/science-run/trajectory?max_points=2")
        assert status == 200
        status, _ = _get(f"{url}/api/v1/runs/..%2Fscience-run/trajectory")
        assert status in {404, 422}


def test_manifest_tampering_prevents_trajectory_and_events(run_dir):
    path = run_dir / "segments" / "00000000" / "trajectory.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(Exception, match="SHA-256 mismatch"):
        query_trajectory(run_dir, start_s=None, end_s=None)
    with pytest.raises(Exception, match="SHA-256 mismatch"):
        query_events(run_dir, start_s=None, end_s=None)
