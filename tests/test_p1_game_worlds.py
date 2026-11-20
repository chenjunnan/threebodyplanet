from __future__ import annotations

import json

from threebody.web.server import list_game_worlds


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_game_worlds_require_matching_completed_environment(tmp_path):
    run = tmp_path / "verified-run"
    run.mkdir()
    _write(run / "long_run_manifest.json", {
        "schema_version": "1", "world_id": "world-1",
        "task": {"status": "COMPLETED", "simulation_time_s": 10},
    })
    environment = tmp_path / "integrated_environments" / "verified-run"
    _write(environment / "integrated_environment_manifest.json", {
        "status": "COMPLETED", "run_id": "verified-run", "world_id": "world-1",
        "model_id": "integrated_environment_v1", "coverage_s": [0, 10],
        "fidelity_mode": "DEFAULT_GAME", "result_sha256": "a" * 64, "grid": [8, 16],
    })
    worlds = list_game_worlds(tmp_path)
    assert len(worlds) == 1
    assert worlds[0]["environment"]["result_sha256"] == "a" * 64

    raw = json.loads((environment / "integrated_environment_manifest.json").read_text())
    raw["world_id"] = "different-world"
    _write(environment / "integrated_environment_manifest.json", raw)
    assert list_game_worlds(tmp_path) == []


def test_game_worlds_exclude_incomplete_or_missing_environment(tmp_path):
    run = tmp_path / "orbit-only"
    run.mkdir()
    _write(run / "long_run_manifest.json", {
        "schema_version": "1", "world_id": "world-2",
        "task": {"status": "COMPLETED", "simulation_time_s": 10},
    })
    assert list_game_worlds(tmp_path) == []
