from __future__ import annotations

import json
import os
import platform
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from pydantic import ValidationError

from threebody import __version__
from threebody.common.constants import CONSTANT_SET_VERSION
from threebody.common.json import sha256_json, write_json_atomic
from threebody.errors import CandidateInvalid, ConfigError
from threebody.physics.nbody.config import load_integration_settings
from threebody.physics.nbody.guard import ReboundInputGuard
from threebody.physics.nbody.models import IntegrationResult, TrajectorySample
from threebody.physics.nbody.rebound_ias15 import (
    IntegrationExecutionError,
    integrate_stream,
)
from threebody.world.models import WorldInitialState


def load_initial_state(path: str | Path) -> WorldInitialState:
    source = Path(path)
    try:
        return WorldInitialState.model_validate_json(
            source.read_text(encoding="utf-8"), strict=True
        )
    except OSError as exc:
        raise ConfigError(f"cannot read initial state {source}: {exc}") from exc
    except ValidationError as exc:
        raise CandidateInvalid(f"invalid initial state JSON {source}: {exc}") from exc


def _write_json_line(handle, sample: TrajectorySample) -> None:
    handle.write(
        json.dumps(
            sample.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    )
    handle.flush()


def _commit_directory(temporary: Path, destination: Path) -> None:
    if destination.exists():
        raise ConfigError(f"simulation output directory already exists: {destination}")
    os.replace(temporary, destination)


def run_simulation_archive(
    initial_path: str | Path, config_path: str | Path, out_dir: str | Path
) -> IntegrationResult:
    initial_source = Path(initial_path)
    initial_state = load_initial_state(initial_source)
    settings, settings_sha256 = load_integration_settings(config_path)
    ReboundInputGuard().validate(initial_state, settings)
    initial_sha256 = sha256_json(initial_state)

    destination = Path(out_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ConfigError(f"simulation output directory already exists: {destination}")
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=str(destination.parent))
    )
    trajectory_path = temporary / "trajectory.jsonl"
    events_path = temporary / "events.json"
    diagnostics_path = temporary / "diagnostics.json"
    settings_path = temporary / "resolved_simulation_config.json"
    manifest_path = temporary / "simulation_manifest.json"
    samples_written = 0
    last_sample_time = settings.start_time_s

    try:
        with trajectory_path.open("w", encoding="utf-8") as trajectory:

            def write_sample(sample: TrajectorySample) -> None:
                nonlocal samples_written, last_sample_time
                _write_json_line(trajectory, sample)
                samples_written += 1
                last_sample_time = sample.t_s

            try:
                run = integrate_stream(initial_state, settings, write_sample)
                status = run.status
                error = run.error
                rebound_version = run.rebound_version
                events = run.events
                diagnostics = run.diagnostic_summary
                minimum_observed_distances = run.minimum_observed_pair_distances_m
                final_time = run.t_final_s
            except IntegrationExecutionError as exc:
                status = "FAILED"
                error = str(exc)
                try:
                    import rebound

                    rebound_version = rebound.__version__
                except ImportError:
                    rebound_version = "unavailable"
                events = ()
                diagnostics = None
                minimum_observed_distances = {}
                final_time = last_sample_time
            trajectory.flush()
            os.fsync(trajectory.fileno())

        write_json_atomic(events_path, [event.model_dump(mode="json") for event in events])
        write_json_atomic(
            diagnostics_path,
            {
                "summary": None if diagnostics is None else diagnostics.model_dump(mode="json"),
                "minimum_observed_pair_distances_m": minimum_observed_distances,
                "distance_semantics": "internal_step_endpoint_observation",
            },
        )
        write_json_atomic(settings_path, settings)
        result = IntegrationResult(
            world_id=initial_state.world_id,
            initial_state_sha256=initial_sha256,
            settings_sha256=settings_sha256,
            integrator_version=rebound_version,
            project_version=__version__,
            python_version=platform.python_version(),
            numpy_version=np.__version__,
            constant_set_version=CONSTANT_SET_VERSION,
            generated_at_utc=datetime.now(timezone.utc).isoformat(),  # noqa: UP017
            settings=settings,
            status=status,
            t_start_s=settings.start_time_s,
            t_final_s=final_time,
            sample_count=samples_written,
            event_count=len(events),
            artifact_refs={
                "initial_source": str(initial_source.resolve()),
                "trajectory": "trajectory.jsonl",
                "events": "events.json",
                "diagnostics": "diagnostics.json",
                "resolved_simulation_config": "resolved_simulation_config.json",
            },
            diagnostic_summary=diagnostics,
            error=error,
        )
        write_json_atomic(manifest_path, result)
        _commit_directory(temporary, destination)
        return result
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

