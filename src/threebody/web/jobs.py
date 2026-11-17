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
from threebody.web.pipeline import derive_run_science
from threebody.world.models import WorldInitialState


@dataclass
class _Job:
    run_id: str
    phase: str = "QUEUED"
    requested: str | None = None
    error: str | None = None
    analysis_status: str = "NOT_STARTED"
    analysis: dict[str, str] | None = None
    analysis_error: str | None = None


class LocalRunController:
    """Bounded, in-process worker for a loopback-only, single-user prototype."""

    def __init__(self, root: Path):
        self.root = root
        self._mutex = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="threebody-m5b")
        self._jobs: dict[str, _Job] = {}
        self._closing = False

    def close(self) -> None:
        # Request a safe boundary stop before waiting for any in-flight segment.
        # Never kill IAS15 while writing a checkpoint or a segment.
        with self._mutex:
            self._closing = True
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
            "analysis_status": job.analysis_status,
            "analysis": job.analysis,
            "analysis_error": job.analysis_error,
            "physical": physical,
            "control_semantics": "requests take effect at a committed segment boundary",
        }

    def _recover_visible_job(self, run_id: str) -> _Job:
        if not isinstance(run_id, str) or run_id in {".", ".."}:
            raise KeyError(run_id)
        from threebody.web.comparison import RUN_ID

        if not RUN_ID.fullmatch(run_id):
            raise KeyError(run_id)
        directory = self.root / run_id
        if directory.is_symlink() or not directory.is_dir():
            raise KeyError(run_id)
        manifest = load_run_manifest(directory)
        archived = manifest.task.status
        phase = "RECOVERY_REQUIRED" if archived == "RUNNING" else archived
        return _Job(run_id, phase=phase)

    def status(self, run_id: str) -> dict[str, Any]:
        with self._mutex:
            job = self._jobs.get(run_id)
            if job is None:
                job = self._recover_visible_job(run_id)
                self._jobs[run_id] = job
            return self._summary(job)

    def analyze(self, run_id: str) -> dict[str, Any]:
        with self._mutex:
            if self._closing:
                raise ConfigError("server is shutting down")
            job = self._jobs.get(run_id)
            if job is None:
                job = self._recover_visible_job(run_id)
                self._jobs[run_id] = job
            if load_run_manifest(self.root / run_id).task.status not in {
                "COMPLETED", "COLLISION"
            }:
                raise ConfigError("only physically terminal archives may be analyzed")
            if job.analysis_status in {"QUEUED", "RUNNING"}:
                return self._summary(job)
            job.analysis_status = "QUEUED"
            job.analysis_error = None
            self._executor.submit(self._postprocess, run_id)
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
            if self._closing:
                raise ConfigError("server is shutting down")
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
            if self._closing:
                raise ConfigError("server is shutting down")
            if load_run_manifest(directory).task.status not in {"PAUSED", "RUNNING"}:
                raise ConfigError("only PAUSED or interrupted RUNNING runs may resume")
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

    def _postprocess(self, run_id: str) -> None:
        with self._mutex:
            self._jobs[run_id].analysis_status = "RUNNING"
        try:
            analysis = derive_run_science(self.root, run_id)
            with self._mutex:
                job = self._jobs[run_id]
                job.analysis = analysis
                job.analysis_status = (
                    "PARTIAL" if analysis["thermal"].startswith("UNAVAILABLE:")
                    else "COMPLETED"
                )
        except Exception as exc:
            with self._mutex:
                job = self._jobs[run_id]
                job.analysis_status = "FAILED"
                job.analysis_error = f"{type(exc).__name__}: {exc}"

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
                        if (
                            scientific_status in {"COMPLETED", "COLLISION"}
                            and (self.root / run_id / "long_run_manifest.json").is_file()
                        ):
                            if self._closing:
                                job.analysis_status = "DEFERRED_SHUTDOWN"
                            else:
                                job.analysis_status = "QUEUED"
                                self._executor.submit(self._postprocess, run_id)
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
