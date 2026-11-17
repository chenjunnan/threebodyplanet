"""Science comparison/export from hash-verified long-run archives, bounded in size."""
from __future__ import annotations

import re
from pathlib import Path

from threebody.common.json import sha256_json
from threebody.errors import ConfigError
from threebody.physics.longrun.reader import SegmentedTrajectoryReader
from threebody.web.outcome_science import read_long_outcome
from threebody.web.thermal_science import read_long_environment

RUN_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
MAX_COMPARISON_RUNS = 8


def compare_runs(root: Path, run_ids: list[str]) -> dict:
    if (
        not isinstance(run_ids, list)
        or not 1 <= len(run_ids) <= MAX_COMPARISON_RUNS
        or any(not isinstance(rid, str) or rid in {".", ".."}
               or not RUN_ID.fullmatch(rid) for rid in run_ids)
        or len(set(run_ids)) != len(run_ids)
    ):
        raise ValueError("provide 1..8 distinct safe run IDs")
    rows = []
    for rid in run_ids:
        run = root / rid
        if run.is_symlink() or not run.is_dir() or run.resolve().parent != root.resolve():
            raise FileNotFoundError(rid)
        reader = SegmentedTrajectoryReader(run)
        # A full scan validates the source chain. No expensive array is retained.
        sample_count = sum(1 for _ in reader.iter_samples())
        event_counts: dict[str, int] = {}
        for event in reader.iter_events():
            event_counts[event.event_type] = event_counts.get(event.event_type, 0) + 1
        manifest = reader.manifest
        row = {
            "run_id": rid, "world_id": manifest.world_id,
            "status": manifest.task.status,
            "simulation_time_s": manifest.task.simulation_time_s,
            "initial_state_sha256": manifest.initial_state_sha256,
            "physics_sha256": manifest.hashes.physics_sha256,
            "schedule_sha256": manifest.hashes.schedule_sha256,
            "trajectory_samples": sample_count,
            "event_counts": event_counts,
            "classification": None, "temperature": None,
            "science_data_status": "TRAJECTORY_VERIFIED",
        }
        envroot = root / "environments"
        env = envroot / rid
        if envroot.is_dir() and not envroot.is_symlink() and env.is_dir():
            if env.is_symlink() or env.resolve().parent != envroot.resolve():
                raise ConfigError("unsafe derived environment directory")
            thermal = read_long_environment(run, env, max_points=2)
            row["temperature"] = {
                "thermal_mode": thermal["thermal_mode"],
                "source_physics_sha256": thermal["source_physics_sha256"],
                "covered_samples": thermal["sample_count"],
            }
        oroot = root / "outcomes"
        outcome = oroot / rid
        if oroot.is_dir() and not oroot.is_symlink() and outcome.is_dir():
            if outcome.is_symlink() or outcome.resolve().parent != oroot.resolve():
                raise ConfigError("unsafe M4 outcome directory")
            result = read_long_outcome(
                run, outcome, env if row["temperature"] is not None else None,
            )
            row["classification"] = result["classification"]
        if row["classification"] is not None:
            row["science_data_status"] = "M4_VERIFIED"
        elif row["temperature"] is not None:
            row["science_data_status"] = "M3_VERIFIED_M4_MISSING"
        rows.append(row)
    return {
        "schema_version": "web_science_comparison_v1",
        "provenance": "verified_immutable_M5A_M3_M4_archives",
        "runs": rows,
        "comparison_sha256": sha256_json(rows),
        "limitations": [
            "Missing temperature/classification means unknown, not stable or habitable",
            "ESCAPE_CANDIDATE is not a proven permanent ejection",
            "Only archived physical time and calculated intervals are represented",
        ],
    }
