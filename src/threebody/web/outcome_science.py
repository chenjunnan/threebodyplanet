"""Verified M5A-derived M4 classification/timeline for the local Web viewer."""
from __future__ import annotations

import json
from pathlib import Path

from threebody.common.json import sha256_json
from threebody.errors import ConfigError
from threebody.experiments.longrun_environment import _sha_file, _source_contract
from threebody.physics.longrun.reader import SegmentedTrajectoryReader
from threebody.physics.outcomes.models import OutcomeEvent
from threebody.web.thermal_science import read_long_environment


def read_long_outcome(
    run_dir: Path, outcome_dir: Path, environment_dir: Path | None,
) -> dict:
    reader = SegmentedTrajectoryReader(run_dir)
    path = outcome_dir / "long_outcome_manifest.json"
    if path.is_symlink():
        raise ConfigError("outcome manifest symlink forbidden")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "0.8.0":
        raise ConfigError("unrecognized long outcome schema")
    if manifest.get("kind") != "M5A_DERIVED_WORLD_OUTCOME":
        raise ConfigError("wrong outcome archive kind")
    if (manifest.get("run_id") != reader.manifest.run_id
            or manifest.get("world_id") != reader.manifest.world_id):
        raise ConfigError("M4 run/world identity differs")
    if manifest.get("m5a_source") != _source_contract(reader):
        raise ConfigError("M4 source M5A segment provenance mismatch")
    env_hash = manifest.get("environment_manifest_sha256")
    if env_hash is not None:
        if environment_dir is None:
            raise ConfigError("M4 classified a thermal archive which is unavailable")
        read_long_environment(run_dir, environment_dir)
        env = json.loads(
            (environment_dir / "long_environment_manifest.json").read_text(encoding="utf-8")
        )
        if sha256_json(env) != env_hash:
            raise ConfigError("M4 thermal source hash mismatch")
    if manifest.get("source_status") != reader.manifest.task.status:
        raise ConfigError("M4 dynamic source status changed")
    refs = manifest.get("artifacts")
    names = (
        "environment_statistics.json",
        "world_classification.json",
        "world_timeline.jsonl",
    )
    if not isinstance(refs, dict) or set(refs) != set(names):
        raise ConfigError("M4 outcome artifact inventory incomplete")
    for name in names:
        file = outcome_dir / name
        if file.is_symlink() or _sha_file(file) != refs[name].get("sha256"):
            raise ConfigError("M4 derived outcome artifact SHA-256 mismatch")
    classification = json.loads(
        (outcome_dir / "world_classification.json").read_text(encoding="utf-8")
    )
    statistics = json.loads(
        (outcome_dir / "environment_statistics.json").read_text(encoding="utf-8")
    )
    timeline = []
    with (outcome_dir / "world_timeline.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            if len(timeline) >= 10000:
                raise ConfigError("too many M4 timeline events for Web viewport")
            event = OutcomeEvent.model_validate_json(line, strict=True)
            timeline.append(event.model_dump(mode="json"))
    return {
        "run_id": reader.manifest.run_id,
        "world_id": reader.manifest.world_id,
        "classification": classification,
        "statistics": statistics,
        "timeline": timeline,
        "source": {
            "m5a": "verified", "thermal": "verified" if env_hash else "missing",
        },
    }
