from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from importlib.resources import files

import pytest

from threebody.web.server import create_console_server
from threebody.web.worldsets import (
    create_worldset, list_worldsets, load_candidate, simulation_settings,
)


def _preset():
    return json.loads(
        files("threebody.web.static").joinpath("world_preset.json").read_text()
    )


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


def test_g21_gallery_reproducible_and_selected_state_is_hash_bound(tmp_path):
    config = _preset()
    config["experiment"]["seed"] = 20261116
    first = create_worldset(tmp_path, config, 3)
    second = create_worldset(tmp_path, config, 3)
    assert first["config_sha256"] == second["config_sha256"]
    assert [c["initial_sha256"] for c in first["candidates"]] == [
        c["initial_sha256"] for c in second["candidates"]
    ]
    assert len(list_worldsets(tmp_path)) == 2
    viable = [c for c in first["candidates"] if c["can_integrate"]]
    assert viable, first["candidates"]
    sample = viable[0]
    state = load_candidate(tmp_path, first["batch_id"], sample["index"])
    assert state.world_id == sample["world_id"]
    assert len(state.bodies) == 4
    with pytest.raises((ValueError, FileNotFoundError)):
        load_candidate(tmp_path, "../bad", 0)
    with pytest.raises((ValueError, FileNotFoundError)):
        load_candidate(tmp_path, first["batch_id"], 100)


def test_bounded_duration_is_not_playback_rate():
    settings = simulation_settings(60.0, 10.0)
    assert settings.time.end_time_s == 60.0
    assert settings.time.output_interval_s == 10.0
    assert settings.backend == "rebound_ias15"
    for duration, interval in [(0, 1), (3155760000, 86400), (86400, 0), (60, 100)]:
        with pytest.raises(ValueError):
            simulation_settings(duration, interval)


def test_web_generate_select_run_and_observe_real_ias15(tmp_path):
    preset = _preset()
    preset["experiment"]["seed"] = 20261116
    with _web(tmp_path) as url:
        code, template = _request(f"{url}/api/v1/worlds/preset")
        assert code == 200
        assert template["world_generation"]["generator"]["version"] == "0.4.1"
        code, batch = _request(
            f"{url}/api/v1/worldsets", {"config": preset, "count": 4}
        )
        assert code == 201, batch
        allowed = next(
            c for c in batch["candidates"] if c["can_integrate"]
        )
        code, accepted = _request(
            f"{url}/api/v1/worlds/run", {
                "batch_id": batch["batch_id"], "candidate_index": allowed["index"],
                "run_id": "web-world-test",
                "duration_s": 60.0, "output_interval_s": 10.0,
            },
        )
        assert code == 202, accepted
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            code, job = _request(f"{url}/api/v1/jobs/web-world-test")
            assert code == 200
            if job["phase"] in {"COMPLETED", "COLLISION", "WORKER_ERROR"}:
                break
            time.sleep(.05)
        else:
            pytest.fail("Web-selected IAS15 task did not complete within test window")
        assert job["phase"] == "COMPLETED", job
        assert job["physical"]["simulation_time_s"] == 60
        code, orbit = _request(
            f"{url}/api/v1/runs/web-world-test/trajectory?max_points=20"
        )
        assert code == 200, orbit
        assert orbit["world_id"] == allowed["world_id"]
        assert orbit["sample_count"] >= 2
        assert orbit["samples"][0]["t_s"] == 0
        assert orbit["samples"][-1]["t_s"] == 60
        code, duplicate = _request(
            f"{url}/api/v1/worlds/run", {
                "batch_id": batch["batch_id"], "candidate_index": allowed["index"],
                "run_id": "web-world-test",
                "duration_s": 60.0, "output_interval_s": 10.0,
            },
        )
        assert code == 422
        assert duplicate["error"] == "invalid_request"


def test_world_endpoints_reject_traversal_and_excessive_count(tmp_path):
    with _web(tmp_path) as url:
        config = _preset()
        for body in [
            {"config": config, "count": 21},
            {"config": {}, "count": 2},
        ]:
            status, response = _request(f"{url}/api/v1/worldsets", body)
            assert status == 422, response
        status, response = _request(
            f"{url}/api/v1/worlds/run", {
                "batch_id": "..", "candidate_index": 0,
                "run_id": "test", "duration_s": 60, "output_interval_s": 10,
            },
        )
        assert status in {404, 422}, response
