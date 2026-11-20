from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from threebody.errors import ConfigError
from threebody.physics.environment.integrated_archive import (
    IntegratedEnvironmentArchiveReader,
)


def _cell_id(latitude_deg: float, longitude_deg: float, latitude_count: int,
             longitude_count: int) -> int:
    if not math.isfinite(latitude_deg) or not -90.0 <= latitude_deg <= 90.0:
        raise ConfigError("environment latitude must be finite and in [-90, 90]")
    if not math.isfinite(longitude_deg) or not -180.0 <= longitude_deg <= 180.0:
        raise ConfigError("environment longitude must be finite and in [-180, 180]")
    latitude_index = min(
        latitude_count - 1,
        max(0, int((math.sin(math.radians(latitude_deg)) + 1.0) * latitude_count / 2.0)),
    )
    normalized_longitude = longitude_deg if longitude_deg < 180.0 else -180.0
    longitude_index = min(
        longitude_count - 1,
        max(0, int((normalized_longitude + 180.0) * longitude_count / 360.0)),
    )
    return latitude_index * longitude_count + longitude_index


def _field(value: Any, unit: str, quality: str) -> dict[str, Any]:
    return {"value": value, "unit": unit, "quality": quality}


def _point_payload(reader: IntegratedEnvironmentArchiveReader, before, after,
                   fraction: float, latitude_deg: float, longitude_deg: float):
    latitude_count, longitude_count = reader.manifest["grid"]
    cell_id = _cell_id(
        latitude_deg, longitude_deg, latitude_count, longitude_count
    )
    first = before.cells[cell_id]
    second = after.cells[cell_id]

    def interpolate(name: str):
        left = getattr(first, name)
        right = getattr(second, name)
        if left is None or right is None:
            return None
        return left + fraction * (right - left)

    categorical = first if fraction < 0.5 else second
    quality = categorical.quality
    gas = {
        name: first.gas_mole_fractions[name] + fraction * (
            second.gas_mole_fractions[name] - first.gas_mole_fractions[name]
        )
        for name in first.gas_mole_fractions
    }
    partial_pressures = {
        name: first.gas_partial_pressures_pa[name] + fraction * (
            second.gas_partial_pressures_pa[name]
            - first.gas_partial_pressures_pa[name]
        )
        for name in first.gas_partial_pressures_pa
    }
    return {
        "schema_version": "environment-point-state-v1",
        "model_id": reader.manifest["model_id"],
        "run_id": reader.manifest["run_id"], "world_id": reader.manifest["world_id"],
        "config_sha256": reader.manifest["config_sha256"],
        "forcing_source_sha256": reader.manifest["forcing_source_sha256"],
        "result_sha256": reader.manifest["result_sha256"],
        "coverage_s": reader.manifest["coverage_s"],
        "fidelity_mode": reader.manifest["fidelity_mode"],
        "query": {"t_s": before.t_s + fraction * (after.t_s - before.t_s),
                  "latitude_deg": latitude_deg, "longitude_deg": longitude_deg},
        "grid_source": {
            "cell_id": cell_id,
            "latitude_center_deg": first.latitude_deg,
            "longitude_center_deg": first.longitude_deg,
            "grid": reader.manifest["grid"],
        },
        "interpolation": {
            "method": "EXACT" if before.t_s == after.t_s else "LINEAR_STATE",
            "source_bracket_s": [before.t_s, after.t_s],
            "categorical_source_t_s": categorical.t_s if hasattr(categorical, "t_s") else (
                before.t_s if fraction < 0.5 else after.t_s
            ),
        },
        "fields": {
            "surface_temperature": _field(
                interpolate("surface_temperature_k"), "K", quality["surface_temperature_k"]
            ),
            "air_temperature": _field(
                interpolate("air_temperature_k"), "K", quality["air_temperature_k"]
            ),
            "surface_pressure": _field(
                interpolate("surface_pressure_pa"), "Pa", quality["surface_pressure_pa"]
            ),
            "air_density": _field(
                interpolate("air_density_kg_m3"), "kg/m3", quality["surface_pressure_pa"]
            ),
            "specific_humidity": _field(
                interpolate("specific_humidity_kg_kg"), "kg/kg", quality["humidity"]
            ),
            "relative_humidity": _field(
                interpolate("relative_humidity"), "1", quality["humidity"]
            ),
            "eastward_wind": _field(
                interpolate("eastward_wind_m_s"), "m/s", quality["wind"]
            ),
            "northward_wind": _field(
                interpolate("northward_wind_m_s"), "m/s", quality["wind"]
            ),
            "cloud_fraction": _field(
                interpolate("cloud_fraction"), "1", quality["precipitation"]
            ),
            "precipitation_rate": _field(
                interpolate("precipitation_rate_kg_m2_s"), "kg/m2/s",
                quality["precipitation"],
            ),
            "liquid_water": _field(
                interpolate("liquid_water_kg_m2"), "kg/m2", "COMPUTED"
            ),
            "ice": _field(interpolate("ice_kg_m2"), "kg/m2", "COMPUTED"),
            "water_vapor": _field(
                interpolate("water_vapor_kg_m2"), "kg/m2", quality["humidity"]
            ),
            "supercritical_water": _field(
                interpolate("supercritical_water_kg_m2"), "kg/m2",
                quality["surface_phase"],
            ),
            "magma_fraction": _field(
                interpolate("magma_fraction"), "1", quality["surface_phase"]
            ),
            "surface_material_liquid_fraction": _field(
                interpolate("surface_material_liquid_fraction"), "1",
                quality["surface_phase"],
            ),
        },
        "gas_mole_fractions": gas,
        "gas_partial_pressures_pa": partial_pressures,
        "weather": {
            "state": categorical.weather_state,
            "precipitation_phase": categorical.precipitation_phase,
            "active_events": list(categorical.active_events),
            "quality": quality["precipitation"],
        },
        "water_phase_regime": categorical.water_phase_regime,
        "uncertainty_flags": sorted(set(
            (*first.uncertainty_flags, *second.uncertainty_flags)
        )),
    }


def query_integrated_environment_point(
    environment_dir: str | Path, m5a_run_dir: str | Path, *, t_s: float,
    latitude_deg: float, longitude_deg: float,
) -> dict[str, Any]:
    reader = IntegratedEnvironmentArchiveReader(environment_dir, m5a_run_dir)
    if reader.manifest["status"] != "COMPLETED":
        raise ConfigError("environment queries require a COMPLETED archive")
    if not math.isfinite(t_s):
        raise ConfigError("environment query time must be finite")
    before = None
    after = None
    for snapshot in reader.iter_snapshots():
        if snapshot.t_s <= t_s:
            before = snapshot
        if snapshot.t_s >= t_s:
            after = snapshot
            break
    if before is None or after is None:
        raise ConfigError("environment query time outside archive coverage")
    fraction = 0.0 if before.t_s == after.t_s else (
        (t_s - before.t_s) / (after.t_s - before.t_s)
    )
    return _point_payload(reader, before, after, fraction, latitude_deg, longitude_deg)


def query_integrated_environment_series(
    environment_dir: str | Path, m5a_run_dir: str | Path, *, latitude_deg: float,
    longitude_deg: float, start_s: float | None = None, end_s: float | None = None,
    max_points: int = 500,
) -> dict[str, Any]:
    if not 2 <= max_points <= 5000:
        raise ConfigError("environment series max_points must be in [2, 5000]")
    if any(
        value is not None and not math.isfinite(value)
        for value in (start_s, end_s)
    ):
        raise ConfigError("environment series times must be finite")
    if start_s is not None and end_s is not None and start_s > end_s:
        raise ConfigError("environment series start exceeds end")
    reader = IntegratedEnvironmentArchiveReader(environment_dir, m5a_run_dir)
    if reader.manifest["status"] != "COMPLETED":
        raise ConfigError("environment queries require a COMPLETED archive")
    snapshots = [
        snapshot for snapshot in reader.iter_snapshots()
        if (start_s is None or snapshot.t_s >= start_s)
        and (end_s is None or snapshot.t_s <= end_s)
    ]
    if not snapshots:
        raise ConfigError("environment series range contains no committed samples")
    if len(snapshots) > max_points:
        indices = sorted(set(
            round(index * (len(snapshots) - 1) / (max_points - 1))
            for index in range(max_points)
        ))
        selected = [snapshots[index] for index in indices]
        sampling = "UNIFORM_INDEX_ENDPOINTS_PRESERVED"
    else:
        selected = snapshots
        sampling = "ALL_COMMITTED_SAMPLES"
    rows = [
        _point_payload(reader, snapshot, snapshot, 0.0, latitude_deg, longitude_deg)
        for snapshot in selected
    ]
    return {
        "schema_version": "environment-series-v1",
        "model_id": reader.manifest["model_id"], "run_id": reader.manifest["run_id"],
        "result_sha256": reader.manifest["result_sha256"],
        "config_sha256": reader.manifest["config_sha256"],
        "forcing_source_sha256": reader.manifest["forcing_source_sha256"],
        "coverage_s": reader.manifest["coverage_s"], "sampling": sampling,
        "source_count": len(snapshots), "returned_count": len(rows), "rows": rows,
        "events": [
            event for event in reader.events()
            if (
                start_s is None
                or event["end_t_s"] is None
                or event["end_t_s"] >= start_s
            )
            and (end_s is None or event["start_t_s"] <= end_s)
        ],
    }


def query_integrated_environment_tile(
    environment_dir: str | Path, m5a_run_dir: str | Path, *,
    time_index: int, level: int,
) -> dict[str, Any]:
    if time_index < 0 or level < 0 or level > 4:
        raise ConfigError("invalid environment tile index or level")
    reader = IntegratedEnvironmentArchiveReader(environment_dir, m5a_run_dir)
    if reader.manifest["status"] != "COMPLETED":
        raise ConfigError("environment queries require a COMPLETED archive")
    snapshot = next(
        (value for value in reader.iter_snapshots() if value.time_index == time_index), None
    )
    if snapshot is None:
        raise ConfigError("unknown environment tile time index")
    source_lat, source_lon = reader.manifest["grid"]
    factor = 2**level
    if source_lat % factor or source_lon % factor:
        raise ConfigError("tile level is incompatible with stored grid")
    target_lat, target_lon = source_lat // factor, source_lon // factor
    fields = (
        "surface_temperature_k", "air_temperature_k", "surface_pressure_pa",
        "relative_humidity", "eastward_wind_m_s", "northward_wind_m_s",
        "cloud_fraction", "precipitation_rate_kg_m2_s", "liquid_water_kg_m2",
        "ice_kg_m2", "magma_fraction",
        "supercritical_water_kg_m2",
    )
    rows = []
    for latitude_index in range(target_lat):
        for longitude_index in range(target_lon):
            cells = []
            for di in range(factor):
                for dj in range(factor):
                    source_index = (
                        (latitude_index * factor + di) * source_lon
                        + longitude_index * factor + dj
                    )
                    cells.append(snapshot.cells[source_index])
            values = {}
            for name in fields:
                available = [getattr(cell, name) for cell in cells
                             if getattr(cell, name) is not None]
                values[name] = None if not available else sum(available) / len(available)
            rows.append({
                "tile_cell_index": latitude_index * target_lon + longitude_index,
                "source_cell_ids": [cell.cell_id for cell in cells],
                "latitude_deg": sum(cell.latitude_deg for cell in cells) / len(cells),
                "longitude_deg": sum(cell.longitude_deg for cell in cells) / len(cells),
                "fields": values,
                "gas_mole_fractions": {
                    name: sum(
                        cell.gas_mole_fractions[name] for cell in cells
                    ) / len(cells)
                    for name in cells[0].gas_mole_fractions
                },
                "gas_partial_pressures_pa": {
                    name: sum(
                        cell.gas_partial_pressures_pa[name] for cell in cells
                    ) / len(cells)
                    for name in cells[0].gas_partial_pressures_pa
                },
                "weather_states": sorted({cell.weather_state for cell in cells}),
                "active_events": sorted({
                    event for cell in cells for event in cell.active_events
                }),
                "quality": {
                    name: sorted({cell.quality[name] for cell in cells})
                    for name in cells[0].quality
                },
                "uncertainty_flags": sorted({
                    flag for cell in cells for flag in cell.uncertainty_flags
                }),
            })
    return {
        "schema_version": "environment-tile-v1",
        "model_id": reader.manifest["model_id"], "run_id": reader.manifest["run_id"],
        "result_sha256": reader.manifest["result_sha256"],
        "config_sha256": reader.manifest["config_sha256"],
        "forcing_source_sha256": reader.manifest["forcing_source_sha256"],
        "coverage_s": reader.manifest["coverage_s"],
        "fidelity_mode": reader.manifest["fidelity_mode"],
        "t_s": snapshot.t_s, "time_index": time_index, "level": level,
        "time_index_range": [
            0, reader.manifest["performance"]["output_count"] - 1
        ],
        "source_grid": [source_lat, source_lon], "grid": [target_lat, target_lon],
        "aggregation": "EQUAL_AREA_ARITHMETIC_MEAN_NO_PHYSICS_RECOMPUTE",
        "units": {
            "surface_temperature_k": "K", "air_temperature_k": "K",
            "surface_pressure_pa": "Pa", "relative_humidity": "1",
            "eastward_wind_m_s": "m/s", "northward_wind_m_s": "m/s",
            "cloud_fraction": "1", "precipitation_rate_kg_m2_s": "kg/m2/s",
            "liquid_water_kg_m2": "kg/m2", "ice_kg_m2": "kg/m2",
            "magma_fraction": "1",
            "supercritical_water_kg_m2": "kg/m2",
            "gas_mole_fractions": "1",
            "gas_partial_pressures_pa": "Pa",
        },
        "cells": rows,
    }
