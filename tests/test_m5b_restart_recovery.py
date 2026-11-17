from __future__ import annotations

import time

import pytest

from threebody.errors import ConfigError
from threebody.physics.longrun.config import load_long_run_settings
from threebody.physics.longrun.models import LongRunSettings
from threebody.physics.longrun.runner import start_long_run
from threebody.web.jobs import LocalRunController
from threebody.world.generators import generate_initial_state


def _wait(controller, run_id, phase, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = controller.status(run_id)
        if status["phase"] == phase:
            return status
        if status["phase"] == "WORKER_ERROR":
            pytest.fail(str(status))
        time.sleep(.02)
    pytest.fail(f"{run_id} never reached {phase}")


def _paused_real_run(tmp_path, resolved):
    config, digest = resolved
    initial = generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )
    settings, _ = load_long_run_settings("configs/m5a_long_run.yaml")
    values = settings.model_dump(mode="json")
    values["time"] = {
        "start_time_s": 0.0, "end_time_s": 60.0, "output_interval_s": 10.0,
    }
    values["segmentation"] = {"simulated_duration_s": 20.0}
    values["checkpoint"] = {
        "simulation_interval_s": 10.0,
        "wall_interval_s": None, "retain_count": 3,
    }
    values["resources"]["max_samples"] = 20
    values["resources"]["max_segments"] = 10
    parsed = LongRunSettings.model_validate(values, strict=True)
    paused = start_long_run(
        initial, parsed, tmp_path / "restarted",
        run_id="restarted", segment_limit=1,
    )
    assert paused.manifest.task.status == "PAUSED"


def test_real_archive_survives_web_server_restart_and_resume(tmp_path, resolved):
    _paused_real_run(tmp_path, resolved)
    first = LocalRunController(tmp_path)
    try:
        status = first.status("restarted")
        assert status["phase"] == "PAUSED"
        assert status["physical"]["committed_segments"] == 1
    finally:
        first.close()
    second = LocalRunController(tmp_path)
    try:
        accepted = second.resume("restarted")
        assert accepted["phase"] in {"QUEUED", "RUNNING", "COMPLETED"}
        done = _wait(second, "restarted", "COMPLETED")
        assert done["physical"]["simulation_time_s"] == 60.0
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status = second.status("restarted")
            if status["analysis_status"] in {"COMPLETED", "PARTIAL", "FAILED"}:
                break
            time.sleep(.02)
        assert status["analysis_status"] == "COMPLETED", status
    finally:
        second.close()
    third = LocalRunController(tmp_path)
    try:
        restored = third.status("restarted")
        assert restored["phase"] == "COMPLETED"
        assert restored["physical"]["simulation_time_s"] == 60
        queued = third.analyze("restarted")
        assert queued["analysis_status"] in {"QUEUED", "RUNNING", "COMPLETED"}
        with pytest.raises(ConfigError):
            third.resume("restarted")
    finally:
        third.close()


def test_unsafe_or_missing_job_is_never_recovered(tmp_path):
    controller = LocalRunController(tmp_path)
    try:
        with pytest.raises(KeyError):
            controller.status("../outside")
        with pytest.raises(KeyError):
            controller.status("missing")
        with pytest.raises(FileNotFoundError):
            controller.resume("missing")
        with pytest.raises(KeyError):
            controller.analyze("missing")
    finally:
        controller.close()
