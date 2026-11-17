"""M5A/M3-derived -> M4 outcome adapter, without manufacturing M2 provenance."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from threebody.common.json import sha256_json, write_json_atomic
from threebody.errors import ConfigError
from threebody.experiments.assessment import _write_timeline
from threebody.experiments.longrun_environment import _sha_file, _source_contract
from threebody.physics.longrun.reader import SegmentedTrajectoryReader
from threebody.physics.outcomes.adapter import M4Inputs, _read_jsonl
from threebody.physics.outcomes.config import load_m4_settings
from threebody.physics.outcomes.engine import assess_world
from threebody.physics.outcomes.models import C0Point, C1Point, FluxPoint
from threebody.web.thermal_science import read_long_environment
from threebody.world.models import WorldInitialState

MAX_ASSESSMENT_SAMPLES = 50000


def assess_long_run(
    run_dir: str | Path, config_path: str | Path,
    out_dir: str | Path, *, environment_dir: str | Path | None = None,
) -> dict:
    source = Path(run_dir)
    reader = SegmentedTrajectoryReader(source)
    if reader.manifest.task.status not in {"COMPLETED", "COLLISION"}:
        raise ConfigError("M4 long-run assessment needs completed or collision-terminated data")
    state_path = source / "initial_state.json"
    state = WorldInitialState.model_validate_json(
        state_path.read_text(encoding="utf-8"), strict=True,
    )
    initial_hash = sha256_json(state)
    if initial_hash != reader.manifest.initial_state_sha256:
        raise ConfigError("M5A initial-state SHA-256 mismatch")
    points = []
    for sample in reader.iter_samples():
        if len(points) >= MAX_ASSESSMENT_SAMPLES:
            raise ConfigError("M4 long-run assessment currently limited to 50000 samples")
        points.append(sample)
    if not points:
        raise ConfigError("M4 requires at least one committed M5A trajectory sample")
    events = tuple(reader.iter_events())
    source_env = None
    env_manifest = None
    flux = ()
    c0 = ()
    c1 = ()
    if environment_dir is not None:
        if reader.manifest.task.status != "COMPLETED":
            raise ConfigError("collision-terminated M5A cannot use completed-only M3 derivative")
        source_env = Path(environment_dir)
        # This checks exact M5A segment provenance, source time-grid and
        # SHA-256 of every derived radiation/temperature artifact before reading.
        read_long_environment(source, source_env)
        env_manifest = json.loads(
            (source_env / "long_environment_manifest.json").read_text(encoding="utf-8")
        )
        flux = _read_jsonl(source_env / "stellar_flux.jsonl", FluxPoint)
        c0 = _read_jsonl(source_env / "c0_temperature.jsonl", C0Point)
        c1 = _read_jsonl(source_env / "c1_latitude_temperature.jsonl", C1Point)
        if len(flux) != len(points):
            raise ConfigError("M5A and derived thermal sample counts differ")
    settings, settings_sha = load_m4_settings(config_path)
    # M4's physics engine requires the same shape as the former M2 adapter.
    # This *in-memory* object uses genuine M5A samples and events only.
    # It is never serialized or labeled as a real M2 archive.
    orbit = SimpleNamespace(
        samples=tuple(points),
        events=events,
        manifest=SimpleNamespace(status=reader.manifest.task.status),
    )
    inputs = M4Inputs(
        initial_path=state_path,
        initial_state=state,
        initial_state_sha256=initial_hash,
        m2_archive=orbit,
        m2_manifest_sha256=None,
        m3_dir=source_env,
        m3_manifest=None if source_env is None else {"status": "COMPLETED"},
        m3_manifest_sha256=None if env_manifest is None else sha256_json(env_manifest),
        flux=flux,
        c0=c0,
        c1=c1,
        energy_budget=None if env_manifest is None else env_manifest["energy_budget"],
        initial_outcomes=(),
    )
    stats, episodes, timeline, classification = assess_world(inputs, settings)
    dest = Path(out_dir)
    if dest.exists():
        raise ConfigError("M4 long-run assessment output already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".m4-long-", dir=dest.parent))
    try:
        write_json_atomic(stage / "environment_statistics.json", {
            "statistics": stats, "exposure_episodes": episodes,
        })
        write_json_atomic(stage / "world_classification.json", classification)
        _write_timeline(stage / "world_timeline.jsonl", timeline)
        artifacts = {
            name: {"sha256": _sha_file(stage / name)}
            for name in (
                "environment_statistics.json", "world_classification.json",
                "world_timeline.jsonl",
            )
        }
        manifest = {
            "schema_version": "0.8.0",
            "kind": "M5A_DERIVED_WORLD_OUTCOME",
            "run_id": reader.manifest.run_id,
            "world_id": reader.manifest.world_id,
            "initial_state_sha256": initial_hash,
            "m5a_source": _source_contract(reader),
            "environment_manifest_sha256": (
                None if env_manifest is None else sha256_json(env_manifest)
            ),
            "m4_settings_sha256": settings_sha,
            "source_status": reader.manifest.task.status,
            "coverage": {
                "start_s": points[0].t_s,
                "end_s": points[-1].t_s,
                "thermal": None if env_manifest is None else {
                    "start_s": env_manifest["t_start_s"],
                    "end_s": env_manifest["t_final_s"],
                },
            },
            "artifacts": artifacts,
            "assumptions": [
                "Existing M4 outcomes engine over genuine M5A trajectory and events",
                "ESCAPE_CANDIDATE is not proof of permanent escape",
                "Missing C0 means DATA_INSUFFICIENT, not temperate",
                "No evolution is fabricated after collision termination",
            ],
        }
        write_json_atomic(stage / "long_outcome_manifest.json", manifest)
        os.replace(stage, dest)
        return manifest
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
