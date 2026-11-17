from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager

import pytest
import yaml

from threebody.common.json import write_json_atomic
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
        thread.join(timeout=5.0)


def _request(url, *, method="GET", payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            return response.status, response.headers, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, json.loads(exc.read())


def test_read_only_api_lists_committed_manifests_and_authoritative_time(tmp_path):
    run = tmp_path / "run-001"
    run.mkdir()
    write_json_atomic(
        run / "long_run_manifest.json",
        {
            "schema_version": "0.6.0",
            "world_id": "candidate-000001",
            "task": {"status": "PAUSED", "simulation_time_s": 12345.0},
        },
    )
    with _server(tmp_path) as base:
        status, headers, payload = _request(f"{base}/api/v1/runs")
        assert status == 200
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert payload["runs"] == [
            {
                "available_manifests": ["long_run_manifest.json"],
                "manifest": "long_run_manifest.json",
                "read_only": True,
                "run_id": "run-001",
                "schema_version": "0.6.0",
                "simulation_time_s": 12345.0,
                "status": "PAUSED",
                "world_id": "candidate-000001",
            }
        ]
        status, _, detail = _request(f"{base}/api/v1/runs/run-001/manifest")
        assert status == 200
        assert detail["manifests"]["long_run_manifest.json"]["task"][
            "simulation_time_s"
        ] == 12345.0


def test_backend_validates_config_and_rejects_unknown_fields(tmp_path, example_path):
    raw = yaml.safe_load(example_path.read_text(encoding="utf-8"))
    with _server(tmp_path) as base:
        status, _, valid = _request(
            f"{base}/api/v1/experiments/validate",
            method="POST",
            payload={"config": raw},
        )
        assert status == 200
        assert valid["valid"] is True
        assert len(valid["sha256"]) == 64
        raw["unexpected"] = True
        status, _, invalid = _request(
            f"{base}/api/v1/experiments/validate",
            method="POST",
            payload={"config": raw},
        )
        assert status == 422
        assert invalid["valid"] is False
        assert "unexpected" in invalid["errors"][0]["message"]


def test_console_rejects_unregistered_task_controls_or_non_loopback_bind(tmp_path):
    with pytest.raises(ValueError, match="loopback"):
        create_console_server(tmp_path, host="0.0.0.0")
    with _server(tmp_path) as base:
        status, _, payload = _request(
            f"{base}/api/v1/runs/run-001/pause", method="POST", payload={}
        )
        assert status == 404
        assert payload["error"] == "run_not_found"
        status, headers, _ = _request(f"{base}/api/v1/health")
        assert status == 200
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


def test_path_traversal_and_symlinked_runs_are_not_exposed(tmp_path):
    outside = tmp_path.parent / "outside-run"
    outside.mkdir(exist_ok=True)
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    with _server(tmp_path) as base:
        status, _, payload = _request(
            f"{base}/api/v1/runs/..%2Foutside-run/manifest"
        )
        assert status in {404, 422}
        status, _, listing = _request(f"{base}/api/v1/runs")
        assert status == 200
        assert listing["runs"] == []


def test_task_start_api_accepts_canonical_json_state_and_settings(
    tmp_path, example_path, monkeypatch
):
    from threebody.config.loader import load_and_resolve
    from threebody.physics.longrun.config import load_long_run_settings
    from threebody.web.jobs import LocalRunController
    from threebody.world.generators import generate_initial_state

    config, digest = load_and_resolve(example_path)
    state = generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )
    settings, _ = load_long_run_settings("configs/m5a_long_run.yaml")
    seen = []

    def capture_start(self, run_id, initial, long_settings):
        seen.append((run_id, initial, long_settings))
        return {"run_id": run_id, "phase": "QUEUED"}

    monkeypatch.setattr(LocalRunController, "start", capture_start)
    with _server(tmp_path) as base:
        status, _, result = _request(
            f"{base}/api/v1/runs",
            method="POST",
            payload={
                "run_id": "test-run",
                "initial_state": state.model_dump(mode="json"),
                "settings": settings.model_dump(mode="json"),
            },
        )
    assert status == 202
    assert result["phase"] == "QUEUED"
    assert seen[0] == ("test-run", state, settings)


def test_task_start_api_rejects_bad_schema_without_starting(tmp_path):
    with _server(tmp_path) as base:
        status, _, payload = _request(
            f"{base}/api/v1/runs",
            method="POST",
            payload={"run_id": "bad", "initial_state": {}, "settings": {}},
        )
        assert status == 422
        assert payload["error"] == "invalid_request"
        status, _, payload = _request(
            f"{base}/api/v1/jobs/unknown"
        )
        assert status == 404
        assert payload["error"] == "run_not_found"
