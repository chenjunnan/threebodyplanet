from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from threebody.errors import ConfigError
from threebody.web import jobs as jobs_module
from threebody.web.jobs import LocalRunController


def _result(status):
    return SimpleNamespace(manifest=SimpleNamespace(task=SimpleNamespace(status=status)))


def _await_phase(controller, run_id, expected, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        actual = controller.status(run_id)
        if actual["phase"] == expected:
            return actual
        time.sleep(0.01)
    pytest.fail(f"job {run_id} did not reach {expected}; last={controller.status(run_id)}")


def test_worker_calls_real_runner_with_one_safe_segment_at_a_time(monkeypatch, tmp_path):
    calls = []
    def start(_state, _settings, directory, *, run_id, segment_limit):
        calls.append(("start", run_id, segment_limit))
        return _result("PAUSED")
    def resume(directory, *, segment_limit):
        calls.append(("resume", directory.name, segment_limit))
        return _result("COMPLETED")
    monkeypatch.setattr(jobs_module, "start_long_run", start)
    monkeypatch.setattr(jobs_module, "resume_long_run", resume)
    controller = LocalRunController(tmp_path)
    try:
        accepted = controller.start("demo", object(), object())
        assert accepted["phase"] in {"QUEUED", "RUNNING", "COMPLETED"}
        final = _await_phase(controller, "demo", "COMPLETED")
        assert final["error"] is None
        assert calls == [("start", "demo", 1), ("resume", "demo", 1)]
    finally:
        controller.close()


def test_pause_request_waits_until_segment_is_safely_committed(monkeypatch, tmp_path):
    started = threading.Event()
    release = threading.Event()
    def start(_state, _settings, directory, *, run_id, segment_limit):
        started.set()
        assert release.wait(timeout=3)
        return _result("PAUSED")
    def should_not_resume(*args, **kwargs):
        pytest.fail("pause must not launch an additional segment")
    monkeypatch.setattr(jobs_module, "start_long_run", start)
    monkeypatch.setattr(jobs_module, "resume_long_run", should_not_resume)
    controller = LocalRunController(tmp_path)
    try:
        controller.start("pause-me", object(), object())
        assert started.wait(timeout=3)
        requested = controller.request("pause-me", "pause")
        assert requested["phase"] == "PAUSE_REQUESTED"
        assert requested["physical"] is None
        with pytest.raises(ConfigError, match="different"):
            controller.request("pause-me", "cancel")
        release.set()
        _await_phase(controller, "pause-me", "PAUSED")
    finally:
        release.set()
        controller.close()


def test_duplicate_jobs_and_unknown_controls_fail(monkeypatch, tmp_path):
    started = threading.Event()
    release = threading.Event()
    def slow_start(*args, **kwargs):
        started.set()
        assert release.wait(timeout=3)
        return _result("COMPLETED")
    monkeypatch.setattr(jobs_module, "start_long_run", slow_start)
    controller = LocalRunController(tmp_path)
    try:
        controller.start("unique", object(), object())
        assert started.wait(timeout=3)
        with pytest.raises(ConfigError, match="active"):
            controller.start("unique", object(), object())
        with pytest.raises(KeyError):
            controller.request("missing", "pause")
        with pytest.raises(ValueError, match="unknown"):
            controller.request("unique", "bogus")
    finally:
        release.set()
        controller.close()
