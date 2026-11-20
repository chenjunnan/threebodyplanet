"""Real five-year M5A -> integrated_environment_v1 acceptance.

This script never shortens a run while labelling it as five years.  A smoke
duration must be requested explicitly and receives a different status.
"""
from __future__ import annotations

import argparse
import json
import math
import runpy
import time
from pathlib import Path

import yaml

from threebody.common.json import write_json_atomic
from threebody.physics.environment.integrated_archive import (
    IntegratedEnvironmentArchiveReader,
    run_integrated_environment_archive,
)
from threebody.physics.environment.integrated_models import IntegratedEnvironmentConfig
from threebody.physics.longrun.archive import verify_run_archive
from threebody.physics.longrun.models import LongRunSettings
from threebody.physics.longrun.reader import SegmentedTrajectoryReader
from threebody.physics.longrun.runner import start_long_run
from threebody.web.integrated_environment_science import (
    query_integrated_environment_point,
    query_integrated_environment_series,
    query_integrated_environment_tile,
)
from threebody.web.worldsets import YEAR_S, simulation_settings

FIVE_YEARS_S = 5.0 * YEAR_S
ORBIT_INTERVAL_S = 21600.0
RUN_ID = "m72-m73-five-year"


def _initial_world():
    module = runpy.run_path(
        str(Path(__file__).with_name("m6_five_year_acceptance.py"))
    )
    return module["initial_world"]()


def _config(path: Path) -> IntegratedEnvironmentConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return IntegratedEnvironmentConfig.model_validate_json(
        json.dumps(raw, allow_nan=False), strict=True
    )


def accept(root: Path, config_path: Path, *, duration_s: float) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    source = root / RUN_ID
    environment = root / "integrated_environments" / RUN_ID
    config = _config(config_path)
    physical_start = time.perf_counter()
    if source.exists():
        verified = verify_run_archive(source)
        if verified.manifest.task.status != "COMPLETED":
            raise RuntimeError("existing M5A source is not COMPLETED")
    else:
        base = simulation_settings(duration_s, ORBIT_INTERVAL_S)
        raw = base.model_dump(mode="json")
        raw["resources"]["max_wall_seconds_per_invocation"] = None
        settings = LongRunSettings.model_validate(raw, strict=True)
        result = start_long_run(
            _initial_world(), settings, source, run_id=RUN_ID
        )
        if result.manifest.task.status != "COMPLETED":
            raise RuntimeError(
                f"real M5A source did not complete: {result.manifest.task.status}"
            )
    physical_wall_s = time.perf_counter() - physical_start
    samples = list(SegmentedTrajectoryReader(source).iter_samples())
    expected = math.ceil(duration_s / ORBIT_INTERVAL_S) + 1
    if len(samples) != expected or samples[-1].t_s != duration_s:
        raise RuntimeError("M5A source sample coverage differs from requested duration")

    environment_start = time.perf_counter()
    manifest = run_integrated_environment_archive(
        source, environment, config, end_t_s=duration_s
    )
    environment_wall_s = time.perf_counter() - environment_start
    if manifest["status"] != "COMPLETED":
        raise RuntimeError("five-year environment archive did not complete")
    reader = IntegratedEnvironmentArchiveReader(environment, source)
    snapshots = list(reader.iter_snapshots())
    if snapshots[-1].t_s != duration_s:
        raise RuntimeError("environment archive does not reach requested physical time")
    last = snapshots[-1]
    middle = snapshots[len(snapshots) // 2]
    point = query_integrated_environment_point(
        environment, source, t_s=middle.t_s, latitude_deg=0.0, longitude_deg=0.0
    )
    series = query_integrated_environment_series(
        environment, source, latitude_deg=0.0, longitude_deg=0.0,
        max_points=500,
    )
    tile = query_integrated_environment_tile(
        environment, source, time_index=middle.time_index, level=0
    )
    hashes = {
        point["result_sha256"], series["result_sha256"], tile["result_sha256"]
    }
    if hashes != {manifest["result_sha256"]}:
        raise RuntimeError("environment query surfaces do not share archive identity")
    diagnostics = manifest["diagnostics"]
    thresholds = {
        "maximum_scaled_energy_residual": 5.0e-4,
        "maximum_water_mass_relative_residual": 1.0e-10,
        "maximum_dry_atmosphere_relative_residual": 1.0e-10,
        "maximum_material_mass_relative_residual": 1.0e-10,
    }
    for name, limit in thresholds.items():
        if diagnostics[name] > limit:
            raise RuntimeError(f"{name} exceeded frozen limit {limit}")
    is_five_year = math.isclose(duration_s, FIVE_YEARS_S, rel_tol=0.0, abs_tol=1e-6)
    report = {
        "status": "PASS" if is_five_year else "SMOKE_ONLY_NOT_FIVE_YEAR_ACCEPTANCE",
        "source": "REAL_REBOUND_IAS15_M5A_CARTESIAN_FORCING",
        "run_id": RUN_ID,
        "duration_s": duration_s,
        "orbit_sample_count": len(samples),
        "environment_output_count": len(snapshots),
        "grid": manifest["grid"],
        "internal_step_s": config.max_internal_step_s,
        "physical_wall_s_this_invocation": physical_wall_s,
        "environment_wall_s_this_invocation": environment_wall_s,
        "environment_performance": manifest["performance"],
        "diagnostics": diagnostics,
        "minimum_surface_temperature_k": min(
            cell.surface_temperature_k for cell in last.cells
        ),
        "maximum_surface_temperature_k": max(
            cell.surface_temperature_k for cell in last.cells
        ),
        "weather_event_count": len(reader.events()),
        "api_result_sha256": manifest["result_sha256"],
        "config_sha256": manifest["config_sha256"],
        "forcing_source_sha256": manifest["forcing_source_sha256"],
        "limitations": [
            "Simplified atmosphere and parameterized wind; not a global circulation model.",
            "Linear interpolation between 6-hour M5A samples cannot resolve "
            "sub-sample irradiation peaks.",
            "Five-year completion is not proof of long-term orbital stability or habitability.",
        ],
    }
    write_json_atomic(root / "m72_m73_five_year_acceptance.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument(
        "--config", type=Path,
        default=Path("configs/m72_m73_five_year_reduced.yaml"),
    )
    parser.add_argument("--smoke-seconds", type=float)
    args = parser.parse_args()
    duration = FIVE_YEARS_S if args.smoke_seconds is None else args.smoke_seconds
    if not 60.0 <= duration <= FIVE_YEARS_S:
        parser.error("duration must be from 60 seconds through five years")
    print(json.dumps(
        accept(args.artifact_root, args.config, duration_s=duration),
        ensure_ascii=False, indent=2,
    ))


if __name__ == "__main__":
    main()
