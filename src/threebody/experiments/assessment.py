from __future__ import annotations

import json
import os
import platform
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from threebody import __version__
from threebody.common.json import sha256_json, write_json_atomic
from threebody.errors import ConfigError
from threebody.physics.outcomes.adapter import load_m4_inputs
from threebody.physics.outcomes.config import load_m4_settings
from threebody.physics.outcomes.engine import assess_world


def _write_timeline(path: Path, events) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(
                json.dumps(
                    event.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            )
        handle.flush()
        os.fsync(handle.fileno())


def run_world_assessment(
    *,
    initial_path: str | Path,
    config_path: str | Path,
    out_dir: str | Path,
    m2_dir: str | Path | None = None,
    m3_dir: str | Path | None = None,
    initial_outcome_path: str | Path | None = None,
) -> dict[str, Any]:
    settings, settings_sha256 = load_m4_settings(config_path)
    inputs = load_m4_inputs(initial_path, m2_dir, m3_dir, initial_outcome_path)
    statistics, episodes, timeline, classification = assess_world(inputs, settings)
    destination = Path(out_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ConfigError(f"M4 output directory already exists: {destination}")
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=str(destination.parent))
    )
    try:
        environment_events = [event for event in timeline if event.source == "M4"]
        write_json_atomic(temporary / "environment_events.json", environment_events)
        write_json_atomic(
            temporary / "environment_statistics.json",
            {
                "schema_version": "0.4.0",
                "statistics": statistics,
                "exposure_episodes": episodes,
                "energy_budget_inherited": inputs.energy_budget,
            },
        )
        write_json_atomic(temporary / "world_classification.json", classification)
        _write_timeline(temporary / "world_timeline.jsonl", timeline)
        write_json_atomic(temporary / "resolved_m4_config.json", settings)
        source_hashes = {
            "initial_state_sha256": inputs.initial_state_sha256,
            "m2_manifest_sha256": inputs.m2_manifest_sha256,
            "m3_manifest_sha256": inputs.m3_manifest_sha256,
            "m4_settings_sha256": settings_sha256,
            "initial_outcome_sha256": (
                None
                if initial_outcome_path is None
                else sha256_json(list(inputs.initial_outcomes))
            ),
        }
        manifest = {
            "schema_version": "0.4.0",
            "world_id": inputs.initial_state.world_id,
            "units": "SI",
            "project_version": __version__,
            "python_version": platform.python_version(),
            "numpy_version": np.__version__,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
            "source_hashes": source_hashes,
            "source_status": {
                "m2": None if inputs.m2_archive is None else inputs.m2_archive.manifest.status,
                "m3": None if inputs.m3_manifest is None else inputs.m3_manifest.get("status"),
                "initial_contact": bool(inputs.initial_outcomes),
            },
            "coverage": {
                "t_start_s": statistics.coverage_start_s,
                "t_final_s": statistics.coverage_end_s,
                "duration_s": statistics.coverage_duration_s,
            },
            "model_versions": {
                "adapter": "m4_source_integrity_v1",
                "exposure": "piecewise_linear_threshold_v1",
                "classification": "multi_axis_world_outcome_v1",
                "ranking": "preference_only_v1",
            },
            "artifact_refs": {
                "events": "environment_events.json",
                "statistics": "environment_statistics.json",
                "classification": "world_classification.json",
                "timeline": "world_timeline.jsonl",
                "resolved_config": "resolved_m4_config.json",
            },
            "scientific_limits": list(classification.narrative_limits),
        }
        write_json_atomic(temporary / "world_outcome_manifest.json", manifest)
        summary = {
            "schema_version": "0.4.0",
            "world_id": inputs.initial_state.world_id,
            "scientific_input_status": classification.scientific_input_status,
            "dynamical_outcome": classification.dynamical_outcome,
            "environmental_outcomes": list(classification.environmental_outcomes),
            "timeline_event_count": len(timeline),
            "selected_by_preferences": classification.selected_by_preferences,
            "selection_score": classification.selection_score,
            "manifest_sha256": sha256_json(manifest),
        }
        write_json_atomic(temporary / "world_assessment_summary.json", summary)
        os.replace(temporary, destination)
        return summary
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
