"""M5A-provenance-aware thermal postprocessing using existing M3 solvers.

Derived archives are immutable and separate from M5A checkpoint/segment storage.
No fake M2 manifest or post-collision extrapolation.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import shutil
import tempfile
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path

import numpy as np

from threebody.common.json import sha256_json, write_json_atomic
from threebody.errors import ConfigError
from threebody.experiments.orbital_reader import EphemerisSample
from threebody.physics.longrun.reader import SegmentedTrajectoryReader
from threebody.physics.nbody.models import BodyTrajectoryState
from threebody.physics.rotation.geometry import PlanetRotation
from threebody.physics.stellar.radiation import (
    StellarRadiation,
    latitude_band_means_vectorized,
    local_top_of_atmosphere_flux,
)
from threebody.physics.thermal.c0 import solve_c0
from threebody.physics.thermal.c1 import solve_c1
from threebody.physics.thermal.config import load_thermal_settings
from threebody.physics.thermal.models import RotationSettings, ThermalSettings
from threebody.world.models import WorldInitialState

MAX_NATIVE_SAMPLES = 50000


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_contract(reader: SegmentedTrajectoryReader) -> dict:
    manifest = reader.manifest
    return {
        "run_id": manifest.run_id,
        "world_id": manifest.world_id,
        "initial_state_sha256": manifest.initial_state_sha256,
        "physics_sha256": manifest.hashes.physics_sha256,
        "schedule_sha256": manifest.hashes.schedule_sha256,
        "segments": [
            {
                "index": entry.segment_index,
                "sha256": entry.segment_manifest_sha256,
                "status": entry.status,
            }
            for entry in manifest.segments if entry.status in {"COMMITTED", "TERMINAL"}
        ],
    }


class _Ephemeris:
    """Cubic Hermite interpolation of verified M5A position/velocity samples."""

    def __init__(self, samples):
        self.samples = samples
        self.times = [sample.t_s for sample in samples]
        if len(samples) < 2:
            raise ConfigError("thermal postprocessing requires at least two orbit samples")

    def sample(self, time: float) -> EphemerisSample:
        if not math.isfinite(time) or time < self.times[0] or time > self.times[-1]:
            raise ConfigError("thermal ephemeris time outside verified M5A coverage")
        right = bisect.bisect_left(self.times, time)
        if right < len(self.times) and self.times[right] == time:
            return EphemerisSample(t_s=time, bodies=self.samples[right].bodies)
        left = right - 1
        if left < 0 or right >= len(self.samples):
            raise ConfigError("cannot interpolate outside source samples")
        first, second = self.samples[left], self.samples[right]
        dt = second.t_s - first.t_s
        u = (time - first.t_s) / dt
        h00 = 2*u**3 - 3*u**2 + 1
        h10 = u**3 - 2*u**2 + u
        h01 = -2*u**3 + 3*u**2
        h11 = u**3 - u**2
        dh00 = (6*u*u - 6*u) / dt
        dh10 = 3*u*u - 4*u + 1
        dh01 = (-6*u*u + 6*u) / dt
        dh11 = 3*u*u - 2*u
        bodies = []
        for a, b in zip(first.bodies, second.bodies, strict=True):
            if a.id != b.id:
                raise ConfigError("M5A segment interpolation body order changed")
            p = (
                h00*np.asarray(a.position_m) + h10*dt*np.asarray(a.velocity_mps)
                + h01*np.asarray(b.position_m) + h11*dt*np.asarray(b.velocity_mps)
            )
            v = (
                dh00*np.asarray(a.position_m) + dh10*np.asarray(a.velocity_mps)
                + dh01*np.asarray(b.position_m) + dh11*np.asarray(b.velocity_mps)
            )
            if not np.all(np.isfinite(p)) or not np.all(np.isfinite(v)):
                raise ConfigError("non-finite M5A Hermite interpolation")
            bodies.append(BodyTrajectoryState(
                id=a.id, position_m=tuple(float(x) for x in p),
                velocity_mps=tuple(float(x) for x in v),
            ))
        return EphemerisSample(t_s=time, bodies=tuple(bodies))


def _write_jsonl(path: Path, records) -> None:
    with path.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(
                record, sort_keys=True, ensure_ascii=False, allow_nan=False,
                separators=(",", ":"),
            ) + "\n")
        output.flush()
        os.fsync(output.fileno())


def derive_long_run_environment(
    run_dir: str | Path, config_path: str | Path, out_dir: str | Path
) -> dict:
    """Compute flux and M3 C0/C1 from a fully verified, completed M5A run."""
    reader = SegmentedTrajectoryReader(run_dir)
    if reader.manifest.task.status != "COMPLETED":
        raise ConfigError(
            "M5A environment derivation currently requires COMPLETED; "
            "no partial/terminal continuation is inferred"
        )
    settings, settings_hash = load_thermal_settings(config_path)
    if not isinstance(settings, ThermalSettings):
        raise ConfigError("invalid M3 thermal model settings")
    state = WorldInitialState.model_validate_json(
        (Path(run_dir) / "initial_state.json").read_text(encoding="utf-8"),
        strict=True,
    )
    if sha256_json(state) != reader.manifest.initial_state_sha256:
        raise ConfigError("M5A source initial-state hash mismatch")
    if settings.planet.id != "planet-1":
        raise ConfigError("M5A postprocessing requires canonical planet-1")
    samples = []
    for sample in reader.iter_samples():
        if len(samples) == MAX_NATIVE_SAMPLES:
            raise ConfigError(
                "M5A environment pass exceeds 50000 verified samples; "
                "process a shorter run or implement incremental thermal checkpoints"
            )
        samples.append(sample)
    if len(samples) < 2:
        raise ConfigError("M5A environment requires at least two samples")
    max_gap = max(
        b.t_s - a.t_s for a, b in zip(samples, samples[1:], strict=False)
    )
    allowed_gap = settings.input.max_orbit_sample_interval_s
    # A schedule exactly on the configured SI-second boundary must not fail
    # because of sub-microsecond floating-point serialization differences.
    if max_gap > allowed_gap and not math.isclose(
        max_gap, allowed_gap, rel_tol=1.0e-12, abs_tol=1.0e-6
    ):
        raise ConfigError(
            "M5A source cadence exceeds M3 maximum interpolation interval: "
            f"verified max gap {max_gap:.9g} s ({max_gap/86400:.3f} days), "
            f"allowed {allowed_gap:.9g} s ({allowed_gap/86400:.3f} days). "
            "This is an orbit-output-data resolution limit, not an IAS15 failure; "
            "reduce orbit output interval for new simulations. Existing completed "
            "archives cannot reconstruct missing orbital extrema exactly."
        )
    # A sparse orbit can be interpolated but its unobserved close passages
    # and irradiation spikes cannot be certified by Hermite interpolation.
    cadence_quality = (
        "COARSE_UNVERIFIED_TEMPORAL_VARIABILITY"
        if max_gap > 86400.0 else "DAILY_OR_FINER"
    )
    cadence_warning = (
        "Orbital sampling exceeds 1 day: piecewise Hermite interpolation may "
        "miss close encounters and rapid irradiation peaks. C0/C1 are "
        "low-confidence approximations, NOT validated high-cadence temperatures; "
        "use <=1-day output for research-grade results."
        if cadence_quality != "DAILY_OR_FINER" else None
    )
    ephemeris = _Ephemeris(tuple(samples))
    radiation = StellarRadiation(state, settings.planet.id)
    planet_body = next(body for body in state.bodies if body.id == settings.planet.id)
    spin = planet_body.properties
    if spin.get("rotation_model") == "kinematic_fixed_axis_v1":
        rotation_settings = RotationSettings.model_validate({
            "axis_inertial_unit": tuple(spin["rotation_axis_inertial_unit"]),
            "prime_meridian_inertial_unit": tuple(spin["prime_meridian_inertial_unit"]),
            "rotation_epoch_s": float(spin["rotation_epoch_s"]),
            "initial_phase_rad": 0.0,
            "rotation_rate_rad_s": float(spin["rotation_rate_rad_s"]),
        }, strict=True)
    else:
        rotation_settings = settings.planet.rotation
    rotation = PlanetRotation(rotation_settings)
    times = [sample.t_s for sample in samples]
    @lru_cache(maxsize=4096)
    def flux_at(t_s: float):
        return radiation.evaluate(ephemeris.sample(t_s))
    physics = settings.thermal
    c0 = None
    c1 = None
    if physics.mode in {"c0", "both"}:
        c0 = solve_c0(
            times_s=times,
            initial_temperature_k=physics.initial_temperature_k,
            planet_center_flux=lambda time: flux_at(time).planet_center_flux_w_m2,
            bond_albedo=physics.bond_albedo,
            effective_emissivity=physics.effective_emissivity,
            areal_heat_capacity_j_m2_k=physics.areal_heat_capacity_j_m2_k,
            internal_heat_flux_w_m2=physics.internal_heat_flux_w_m2,
            integrator=physics.integrator,
        )
    if physics.mode in {"c1", "both"}:
        def zonal(time: float, _latitude) -> np.ndarray:
            return latitude_band_means_vectorized(
                flux_at(time),
                physics.c1.latitude_bands,
                rotation_settings.axis_inertial_unit,
            )
        c1 = solve_c1(
            times_s=times, initial_temperature_k=physics.initial_temperature_k,
            zonal_incoming=zonal, latitude_bands=physics.c1.latitude_bands,
            bond_albedo=physics.bond_albedo,
            effective_emissivity=physics.effective_emissivity,
            areal_heat_capacity_j_m2_k=physics.areal_heat_capacity_j_m2_k,
            internal_heat_flux_w_m2=physics.internal_heat_flux_w_m2,
            diffusivity_w_m2_k=physics.c1.diffusivity_w_m2_k,
            integrator=physics.integrator,
        )
    flux_rows = []
    for t in times:
        snapshot = flux_at(t)
        record = asdict(snapshot)
        normal = rotation.normal(0.0, 0.0, t)
        record["reference_equator_prime_meridian_normal"] = normal
        record["reference_equator_prime_meridian_toa_w_m2"] = (
            local_top_of_atmosphere_flux(snapshot, normal)
        )
        flux_rows.append(record)
    destination = Path(out_dir)
    if destination.exists():
        raise ConfigError(f"derived environment output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{destination.name}.tmp-",dir=destination.parent))
    try:
        filenames = {
            "flux": "stellar_flux.jsonl",
            "c0": "c0_temperature.jsonl",
            "c1": "c1_latitude_temperature.jsonl",
        }
        _write_jsonl(temp / filenames["flux"], flux_rows)
        _write_jsonl(temp / filenames["c0"], () if c0 is None else (
            asdict(point) for point in c0.samples
        ))
        _write_jsonl(temp / filenames["c1"], () if c1 is None else (
            asdict(point) for point in c1.samples
        ))
        manifest = {
            "schema_version": "0.7.0",
            "kind": "M5A_DERIVED_ENVIRONMENT",
            "world_id": state.world_id,
            "run_id": reader.manifest.run_id,
            "source": _source_contract(reader),
            "thermal_settings_sha256": settings_hash,
            "thermal_mode": physics.mode,
            "t_start_s": times[0],
            "t_final_s": times[-1],
            "sample_count": len(times),
            "source_max_orbit_gap_s": max_gap,
            "sampling_quality": cadence_quality,
            "sampling_warning": cadence_warning,
            "time_grid": "exact_m5a_committed_output_samples",
            "interpolation": "piecewise_cubic_hermite_position_velocity",
            "solver": {
                "c0": None if c0 is None else c0.solver,
                "c1": None if c1 is None else c1.solver,
            },
            "energy_budget": {
                "c0": None if c0 is None else asdict(c0.energy_budget),
                "c1": None if c1 is None else asdict(c1.energy_budget),
            },
            "artifacts": {
                kind: {"filename":name, "sha256":_sha_file(temp/name)}
                for kind,name in filenames.items()
            },
            "assumptions": [
                "three isotropic bolometric point stars; no stellar occultation",
                "C0/C1 greybody thermal models; not weather or proof of habitability",
                "Hermite orbit interpolation of verified M5A samples is not N-body integration",
                "no thermal forcing extrapolated beyond source coverage",
                "sparse orbit cadence may miss unobserved irradiation extremes; "
                "sampling quality and gap are explicit in this manifest",
                "planet spin axis uses archived world metadata when present; "
                "stellar spins are kinematic only, isotropic bolometric emission",
            ],
        }
        write_json_atomic(temp / "long_environment_manifest.json", manifest)
        os.replace(temp,destination)
        return manifest
    except Exception:
        shutil.rmtree(temp,ignore_errors=True)
        raise
