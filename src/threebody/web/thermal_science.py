"""Read-only verified M5A-linked thermal view. No M2 manifest forgery."""
from __future__ import annotations

import json
import math
from pathlib import Path

from threebody.errors import ConfigError
from threebody.experiments.longrun_environment import _sha_file, _source_contract
from threebody.physics.longrun.reader import SegmentedTrajectoryReader
from threebody.physics.outcomes.models import C0Point, C1Point, FluxPoint

MAX_VIEW_POINTS = 2000


def _read_series(path: Path, model, count: int) -> list:
    result = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                raise ConfigError("blank thermal JSONL row")
            if len(result) >= count:
                raise ConfigError("derived thermal series exceeds declared count")
            result.append(model.model_validate_json(line, strict=True))
    return result


def read_long_environment(
    run_dir: Path, environment_dir: Path, *,
    start_s: float | None = None, end_s: float | None = None,
    max_points: int = 500,
) -> dict:
    if not 2 <= max_points <= MAX_VIEW_POINTS:
        raise ValueError("max_points must be within 2..2000")
    if any(value is not None and not math.isfinite(value) for value in (start_s, end_s)):
        raise ValueError("time boundaries must be finite")
    if start_s is not None and end_s is not None and start_s > end_s:
        raise ValueError("time range reversed")
    reader = SegmentedTrajectoryReader(run_dir)
    if reader.manifest.task.status != "COMPLETED":
        raise ConfigError("source run is not completed; derived archive is not current")
    manifest_path = environment_dir / "long_environment_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "0.7.0":
        raise ConfigError("invalid M5A derived environment schema")
    if manifest.get("kind") != "M5A_DERIVED_ENVIRONMENT":
        raise ConfigError("unrecognized thermal origin")
    if manifest.get("run_id") != reader.manifest.run_id:
        raise ConfigError("derived environment run ID differs")
    if manifest.get("world_id") != reader.manifest.world_id:
        raise ConfigError("derived environment world ID differs")
    if manifest.get("source") != _source_contract(reader):
        raise ConfigError("M5A provenance chain changed since thermal derivation")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {"flux", "c0", "c1"}:
        raise ConfigError("derived environment artifact manifest incomplete")
    models = {"flux": FluxPoint, "c0": C0Point, "c1": C1Point}
    expected_names = {
        "flux": "stellar_flux.jsonl",
        "c0": "c0_temperature.jsonl",
        "c1": "c1_latitude_temperature.jsonl",
    }
    series = {}
    count = manifest["sample_count"]
    if not isinstance(count, int) or not 2 <= count <= 5000:
        raise ConfigError("derived thermal sample count out of range")
    for key, model in models.items():
        info = artifacts[key]
        if not isinstance(info, dict) or info.get("filename") != expected_names[key]:
            raise ConfigError("unexpected thermal file reference")
        path = environment_dir / expected_names[key]
        if path.is_symlink() or _sha_file(path) != info.get("sha256"):
            raise ConfigError("derived thermal artifact SHA-256 mismatch")
        series[key] = _read_series(path, model, count)
    flux = series["flux"]
    if len(flux) != count:
        raise ConfigError("derived thermal flux sample count mismatch")
    times = [point.t_s for point in flux]
    if times[0] != manifest["t_start_s"] or times[-1] != manifest["t_final_s"]:
        raise ConfigError("derived thermal physical coverage differs")
    if any(a >= b for a, b in zip(times, times[1:], strict=False)):
        raise ConfigError("derived thermal times must be increasing")
    source_times = [sample.t_s for sample in reader.iter_samples()]
    if times != source_times:
        raise ConfigError("derived thermal time grid differs from verified M5A samples")
    for key in ("c0", "c1"):
        points = series[key]
        if points and [p.t_s for p in points] != times:
            raise ConfigError("derived thermal temperature times differ from flux")
    mode = manifest["thermal_mode"]
    if mode not in {"both", "c0", "c1"}:
        raise ConfigError("invalid derived thermal mode")
    if (mode in {"both", "c0"}) != bool(series["c0"]):
        raise ConfigError("C0 absence/presence contradicts thermal mode")
    if (mode in {"both", "c1"}) != bool(series["c1"]):
        raise ConfigError("C1 absence/presence contradicts thermal mode")
    eligible = [
        i for i,t in enumerate(times)
        if (start_s is None or t >= start_s) and (end_s is None or t <= end_s)
    ]
    if len(eligible) > max_points:
        eligible = [
            eligible[i*(len(eligible)-1)//(max_points-1)]
            for i in range(max_points)
        ]
    rows = []
    for index in eligible:
        flux_point = flux[index]
        c0_point = series["c0"][index] if series["c0"] else None
        c1_point = series["c1"][index] if series["c1"] else None
        rows.append({
            "t_s": times[index],
            "flux_w_m2": {
                **{star.body_id: star.flux_w_m2 for star in flux_point.stars},
                "total": flux_point.planet_center_flux_w_m2,
            },
            "c0_temperature_k": None if c0_point is None else c0_point.temperature_k,
            "c1_global_mean_k": (
                None if c1_point is None else c1_point.global_mean_temperature_k
            ),
            "c1_latitude_rad": (
                None if c1_point is None else list(c1_point.latitude_center_rad)
            ),
            "c1_temperature_k": (
                None if c1_point is None else list(c1_point.temperature_k)
            ),
        })
    return {
        "run_id": reader.manifest.run_id,
        "world_id": reader.manifest.world_id,
        "thermal_mode": mode,
        "source_physics_sha256": reader.manifest.hashes.physics_sha256,
        "sample_count": len(times),
        "display_count": len(rows),
        "time_grid": manifest["time_grid"],
        "temperature_missing_is_null": True,
        "interpolation": manifest["interpolation"],
        "rows": rows,
    }
