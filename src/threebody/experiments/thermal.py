from __future__ import annotations

import json
import math
import os
import platform
import shutil
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
import scipy

from threebody import __version__
from threebody.common.json import sha256_json, write_json_atomic
from threebody.errors import ConfigError, ThermalExecutionError
from threebody.experiments.orbital_reader import M2TrajectoryReader
from threebody.physics.rotation.geometry import PlanetRotation
from threebody.physics.stellar.radiation import (
    StellarRadiation,
    latitude_band_mean_top_of_atmosphere_flux,
    local_top_of_atmosphere_flux,
)
from threebody.physics.thermal.c0 import solve_c0
from threebody.physics.thermal.c1 import equal_area_grid, solve_c1
from threebody.physics.thermal.config import load_thermal_settings
from threebody.physics.thermal.diagnostics import THERMAL_CONSTANT_SET_VERSION


def _resolve_input(config_path: Path, configured_path: str) -> Path:
    path = Path(configured_path)
    return path if path.is_absolute() else config_path.parent / path


def _output_times(start_s: float, end_s: float, interval_s: float, maximum: int) -> list[float]:
    required = math.ceil((end_s - start_s) / interval_s) + 1
    if required > maximum:
        raise ConfigError(
            f"thermal output requires {required} samples, exceeding max_samples={maximum}"
        )
    times = [start_s]
    index = 1
    while times[-1] < end_s:
        times.append(min(start_s + index * interval_s, end_s))
        index += 1
    return times


def _write_json_lines(path: Path, values) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for value in values:
            handle.write(
                json.dumps(
                    value,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            )
        handle.flush()
        os.fsync(handle.fileno())


def run_thermal_archive(
    config_path: str | Path, out_dir: str | Path
) -> dict[str, object]:
    config_source = Path(config_path)
    settings, settings_sha256 = load_thermal_settings(config_source)
    initial_path = _resolve_input(config_source, settings.input.initial_state)
    m2_archive = _resolve_input(config_source, settings.input.m2_archive)
    reader = M2TrajectoryReader(
        initial_path,
        m2_archive,
        allow_collision_terminated=settings.input.allow_collision_terminated,
        max_sample_interval_s=settings.input.max_orbit_sample_interval_s,
    )
    radiation = StellarRadiation(reader.initial_state, settings.planet.id)
    rotation = PlanetRotation(settings.planet.rotation)
    times = _output_times(
        reader.t_start_s,
        reader.t_end_s,
        settings.output.sample_interval_s,
        settings.output.max_samples,
    )
    destination = Path(out_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ConfigError(f"thermal output directory already exists: {destination}")
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=str(destination.parent))
    )

    @lru_cache(maxsize=4096)
    def flux_at(t_s: float):
        return radiation.evaluate(reader.sample(t_s))

    c0_result = None
    c1_result = None
    error = None
    status = "COMPLETED"
    try:
        try:
            if settings.thermal.mode in {"c0", "both"}:
                c0_result = solve_c0(
                    times_s=times,
                    initial_temperature_k=settings.thermal.initial_temperature_k,
                    planet_center_flux=lambda time: flux_at(time).planet_center_flux_w_m2,
                    bond_albedo=settings.thermal.bond_albedo,
                    effective_emissivity=settings.thermal.effective_emissivity,
                    areal_heat_capacity_j_m2_k=(
                        settings.thermal.areal_heat_capacity_j_m2_k
                    ),
                    internal_heat_flux_w_m2=(
                        settings.thermal.internal_heat_flux_w_m2
                    ),
                    integrator=settings.thermal.integrator,
                )
            if settings.thermal.mode in {"c1", "both"}:
                band_count = settings.thermal.c1.latitude_bands
                x, _, dx = equal_area_grid(band_count)

                def zonal_forcing(time: float, _latitudes: np.ndarray) -> np.ndarray:
                    snapshot = flux_at(time)
                    return np.asarray(
                        [
                            latitude_band_mean_top_of_atmosphere_flux(
                                snapshot,
                                float(center - 0.5 * dx),
                                float(center + 0.5 * dx),
                                settings.planet.rotation.axis_inertial_unit,
                            )
                            for center in x
                        ]
                    )

                c1_result = solve_c1(
                    times_s=times,
                    initial_temperature_k=settings.thermal.initial_temperature_k,
                    zonal_incoming=zonal_forcing,
                    latitude_bands=band_count,
                    bond_albedo=settings.thermal.bond_albedo,
                    effective_emissivity=settings.thermal.effective_emissivity,
                    areal_heat_capacity_j_m2_k=(
                        settings.thermal.areal_heat_capacity_j_m2_k
                    ),
                    internal_heat_flux_w_m2=(
                        settings.thermal.internal_heat_flux_w_m2
                    ),
                    diffusivity_w_m2_k=(
                        settings.thermal.c1.diffusivity_w_m2_k
                    ),
                    integrator=settings.thermal.integrator,
                )
        except (ConfigError, ThermalExecutionError) as exc:
            status = "FAILED"
            error = str(exc)
            c0_result = None
            c1_result = None

        flux_records = []
        if status == "COMPLETED":
            for time in times:
                snapshot = flux_at(time)
                record = asdict(snapshot)
                normal = rotation.normal(0.0, 0.0, time)
                record["reference_equator_prime_meridian_normal"] = normal
                record["reference_equator_prime_meridian_toa_w_m2"] = (
                    local_top_of_atmosphere_flux(snapshot, normal)
                )
                flux_records.append(record)
        _write_json_lines(temporary / "stellar_flux.jsonl", flux_records)
        _write_json_lines(
            temporary / "c0_temperature.jsonl",
            [] if c0_result is None else (asdict(sample) for sample in c0_result.samples),
        )
        _write_json_lines(
            temporary / "c1_latitude_temperature.jsonl",
            [] if c1_result is None else (asdict(sample) for sample in c1_result.samples),
        )
        energy_budget = {
            "c0": None if c0_result is None else asdict(c0_result.energy_budget),
            "c1": None if c1_result is None else asdict(c1_result.energy_budget),
            "c1_maximum_absolute_global_transport_imbalance_w_m2": (
                None
                if c1_result is None
                else c1_result.maximum_absolute_global_transport_imbalance_w_m2
            ),
        }
        write_json_atomic(temporary / "energy_budget.json", energy_budget)
        write_json_atomic(temporary / "resolved_thermal_config.json", settings)
        manifest: dict[str, object] = {
            "schema_version": "0.3.0",
            "units": "SI",
            "status": status,
            "error": error,
            "world_id": reader.initial_state.world_id,
            "project_version": __version__,
            "python_version": platform.python_version(),
            "numpy_version": np.__version__,
            "scipy_version": scipy.__version__,
            "thermal_constant_set_version": THERMAL_CONSTANT_SET_VERSION,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
            "settings_sha256": settings_sha256,
            "initial_state_sha256": sha256_json(reader.initial_state),
            "m2_manifest_sha256": reader.manifest_sha256,
            "m2_status": reader.manifest.status,
            "input_refs": {
                "initial_state": str(initial_path.resolve()),
                "m2_archive": str(m2_archive.resolve()),
            },
            "t_start_s": reader.t_start_s,
            "t_final_s": reader.t_end_s if status == "COMPLETED" else reader.t_start_s,
            "requested_t_final_s": reader.t_end_s,
            "sample_count": len(flux_records),
            "thermal_mode": settings.thermal.mode,
            "integrators": {
                "c0": None if c0_result is None else c0_result.solver,
                "c1": None if c1_result is None else c1_result.solver,
                "rtol": settings.thermal.integrator.rtol,
                "atol_k": settings.thermal.integrator.atol_k,
                "max_step_s": settings.thermal.integrator.max_step_s,
            },
            "model_versions": {
                "orbit_interpolation": reader.version,
                "radiation": "isotropic_bolometric_v1",
                "rotation": "rigid_fixed_axis_v1",
                "c0": "greybody_global_energy_balance_v1",
                "c1": "equal_area_finite_volume_latitudinal_v1",
            },
            "orbit_interpolation": asdict(reader.diagnostics),
            "rotation_phase_reference": {
                "rotation_epoch_s": settings.planet.rotation.rotation_epoch_s,
                "initial_phase_rad": settings.planet.rotation.initial_phase_rad,
                "rotation_rate_rad_s": settings.planet.rotation.rotation_rate_rad_s,
                "large_epoch_reduction": "decimal_modulo_tau",
            },
            "model_assumptions": [
                "three isotropic bolometric point sources",
                "stellar occultation and eclipses disabled",
                "fixed rigid rotation axis without tides or precession",
                "grey-body C0/C1 temperatures are not local weather",
                "C1 forcing is longitude- and finite-volume-band averaged",
                "M2 Hermite interpolation is not a new N-body integration",
            ],
            "artifact_refs": {
                "stellar_flux": "stellar_flux.jsonl",
                "c0_temperature": "c0_temperature.jsonl",
                "c1_latitude_temperature": "c1_latitude_temperature.jsonl",
                "energy_budget": "energy_budget.json",
                "resolved_config": "resolved_thermal_config.json",
            },
        }
        write_json_atomic(temporary / "thermal_manifest.json", manifest)
        os.replace(temporary, destination)
        return manifest
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

