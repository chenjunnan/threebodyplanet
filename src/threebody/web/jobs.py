"""Local single-user M5B job orchestration. M5A remains the authoritative solver.

A pause/cancel request is cooperative: the current immutable segment finishes first.
No worker writes to a run concurrently with another worker in this process.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from threebody.errors import ConfigError
from threebody.physics.longrun.archive import (
    SingleWriterLock,
    load_run_manifest,
    set_task_status,
)
from threebody.physics.longrun.models import LongRunSettings
from threebody.physics.longrun.runner import resume_long_run, start_long_run
from threebody.world.models import WorldInitialState


@dataclass
class _Job:
    run_id: str
    phase: str = "QUEUED"
    requested: str | None = None
    error: str | None = None


class LocalRunController:
    """Bounded, in-process worker for a loopback-only, single-user prototype."""

    def __init__(self, root: Path):
        self.root = root
        self._mutex = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="threebody-m5b")
        self._jobs: dict[str, _Job] = {}

    def close(self) -> None:
        # Request a safe boundary stop before waiting for any in-flight segment.
        # Never kill IAS15 while writing a checkpoint or a segment.
        with self._mutex:
            for job in self._jobs.values():
                if job.phase in {"QUEUED", "RUNNING", "PAUSE_REQUESTED"}:
                    job.requested = "PAUSE_REQUESTED"
                    job.phase = "PAUSE_REQUESTED"
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _summary(self, job: _Job) -> dict[str, Any]:
        directory = self.root / job.run_id
        physical: dict[str, Any] | None = None
        if (directory / "long_run_manifest.json").is_file():
            manifest = load_run_manifest(directory)
            physical = {
                "status": manifest.task.status,
                "simulation_time_s": manifest.task.simulation_time_s,
                "committed_segments": sum(
                    item.status in {"COMMITTED", "TERMINAL"}
                    for item in manifest.segments
                ),
                "planned_segments": len(manifest.segments),
            }
        return {
            "run_id": job.run_id,
            "phase": job.phase,
            "requested": job.requested,
            "error": job.error,
            "physical": physical,
            "control_semantics": "requests take effect at a committed segment boundary",
        }

    def status(self, run_id: str) -> dict[str, Any]:
        with self._mutex:
            job = self._jobs.get(run_id)
            if job is None:
                raise KeyError(run_id)
            return self._summary(job)

    def _register(self, run_id: str) -> None:
        if run_id in self._jobs and self._jobs[run_id].phase in {
            "QUEUED", "RUNNING", "PAUSE_REQUESTED", "CANCEL_REQUESTED"
        }:
            raise ConfigError("this run already has an active or queued worker")
        self._jobs[run_id] = _Job(run_id)

    def start(
        self, run_id: str, state: WorldInitialState, settings: LongRunSettings
    ) -> dict[str, Any]:
        with self._mutex:
            if (self.root / run_id).exists():
                raise ConfigError("run_id already exists; never overwrite an archive")
            self._register(run_id)
            self._executor.submit(self._worker, run_id, state, settings)
            return self._summary(self._jobs[run_id])

    def resume(self, run_id: str) -> dict[str, Any]:
        with self._mutex:
            directory = self.root / run_id
            if directory.is_symlink() or not directory.is_dir():
                raise FileNotFoundError(run_id)
            if load_run_manifest(directory).task.status != "PAUSED":
                raise ConfigError("only safely PAUSED runs may resume from the Web")
            self._register(run_id)
            self._executor.submit(self._worker, run_id, None, None)
            return self._summary(self._jobs[run_id])

    def request(self, run_id: str, action: str) -> dict[str, Any]:
        if action not in {"pause", "cancel"}:
            raise ValueError("unknown action")
        with self._mutex:
            job = self._jobs.get(run_id)
            if job is None:
                raise KeyError(run_id)
            if job.phase not in {"QUEUED", "RUNNING", "PAUSE_REQUESTED", "CANCEL_REQUESTED"}:
                raise ConfigError("no active job is available for this action")
            requested = "PAUSE_REQUESTED" if action == "pause" else "CANCEL_REQUESTED"
            if job.requested is not None and job.requested != requested:
                raise ConfigError("a different terminal/control request is already pending")
            job.requested = requested
            job.phase = requested
            return self._summary(job)

    def _persist_cancelled(self, run_id: str) -> None:
        directory = self.root / run_id
        manifest = load_run_manifest(directory)
        if manifest.task.status != "PAUSED":
            raise ConfigError("cancel may only commit after a safe PAUSED segment boundary")
        with SingleWriterLock(directory, run_id) as lock:
            set_task_status(directory, "CANCELLED", lock_token=lock.token)

    def _worker(
        self,
        run_id: str,
        state: WorldInitialState | None,
        settings: LongRunSettings | None,
    ) -> None:
        try:
            with self._mutex:
                job = self._jobs[run_id]
                if job.requested == "CANCEL_REQUESTED" and state is not None:
                    job.phase = "CANCELLED_BEFORE_START"
                    return
                job.phase = "RUNNING"
            initial = state is not None
            while True:
                if initial:
                    assert state is not None and settings is not None
                    result = start_long_run(
                        state, settings, self.root / run_id,
                        run_id=run_id, segment_limit=1,
                    )
                    initial = False
                else:
                    result = resume_long_run(self.root / run_id, segment_limit=1)
                with self._mutex:
                    job = self._jobs[run_id]
                    requested = job.requested
                    scientific_status = result.manifest.task.status
                    if scientific_status in {"COMPLETED", "COLLISION", "FAILED", "CANCELLED"}:
                        job.phase = scientific_status
                        return
                    if requested == "PAUSE_REQUESTED":
                        job.phase = "PAUSED"
                        return
                    if requested == "CANCEL_REQUESTED":
                        self._persist_cancelled(run_id)
                        job.phase = "CANCELLED"
                        return
                    job.phase = "RUNNING"
        except Exception as exc:
            with self._mutex:
                job = self._jobs[run_id]
                job.phase = "WORKER_ERROR"
                job.error = f"{type(exc).__name__}: {exc}"
