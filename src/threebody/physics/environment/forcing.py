from __future__ import annotations

import bisect
import math
from pathlib import Path
from typing import Protocol

import numpy as np

from threebody.common.json import sha256_json
from threebody.errors import ConfigError
from threebody.physics.environment.models import EnvironmentForcingValue
from threebody.physics.longrun.reader import SegmentedTrajectoryReader
from threebody.world.models import WorldInitialState


class EnvironmentForcing(Protocol):
    coverage_s: tuple[float, float]
    source_sha256: str
    kind: str

    def sample(self, t_s: float) -> EnvironmentForcingValue: ...


class ConstantForcing:
    kind = "CONSTANT_BENCHMARK"

    def __init__(self, absorbed_flux_w_m2: float, *, albedo: float = 0.0):
        if not math.isfinite(absorbed_flux_w_m2) or absorbed_flux_w_m2 < 0.0:
            raise ConfigError("constant absorbed flux must be finite and nonnegative")
        if not math.isfinite(albedo) or not 0.0 <= albedo < 1.0:
            raise ConfigError("constant forcing albedo must be in [0, 1)")
        self.absorbed = absorbed_flux_w_m2
        self.albedo = albedo
        self.coverage_s = (-math.inf, math.inf)
        self.source_sha256 = sha256_json(
            {"kind": self.kind, "absorbed_flux_w_m2": self.absorbed, "albedo": albedo}
        )

    def sample(self, t_s: float) -> EnvironmentForcingValue:
        if not math.isfinite(t_s):
            raise ConfigError("forcing time must be finite")
        return EnvironmentForcingValue(
            t_s=t_s,
            top_of_atmosphere_flux_w_m2=self.absorbed / (1.0 - self.albedo),
            absorbed_flux_w_m2=self.absorbed,
            interpolation="CONSTANT",
            source_bracket_s=None,
        )


class StepForcing:
    kind = "STEP_BENCHMARK"

    def __init__(self, before_w_m2: float, after_w_m2: float, step_time_s: float):
        if any(not math.isfinite(v) for v in (before_w_m2, after_w_m2, step_time_s)):
            raise ConfigError("step forcing values must be finite")
        if min(before_w_m2, after_w_m2) < 0.0:
            raise ConfigError("step forcing must be nonnegative")
        self.before = before_w_m2
        self.after = after_w_m2
        self.step = step_time_s
        self.coverage_s = (-math.inf, math.inf)
        self.source_sha256 = sha256_json(
            {"kind": self.kind, "before": before_w_m2, "after": after_w_m2,
             "step_time_s": step_time_s}
        )

    def sample(self, t_s: float) -> EnvironmentForcingValue:
        if not math.isfinite(t_s):
            raise ConfigError("forcing time must be finite")
        value = self.before if t_s < self.step else self.after
        return EnvironmentForcingValue(
            t_s=t_s,
            top_of_atmosphere_flux_w_m2=value,
            absorbed_flux_w_m2=value,
            interpolation="STEP",
            source_bracket_s=None,
        )


class M5AOrbitForcing:
    """Verified scalar global-mean forcing sampled from an immutable M5A run."""

    kind = "M5A_THREE_STAR_LINEAR_FLUX_V1"

    def __init__(
        self, times_s: tuple[float, ...], toa_fluxes_w_m2: tuple[float, ...],
        *, albedo: float, source_contract: dict,
    ):
        if len(times_s) < 2 or len(times_s) != len(toa_fluxes_w_m2):
            raise ConfigError("M5A forcing requires at least two matching samples")
        if any(b <= a for a, b in zip(times_s, times_s[1:], strict=False)):
            raise ConfigError("M5A forcing times must be strictly increasing")
        if any(not math.isfinite(v) or v < 0.0 for v in toa_fluxes_w_m2):
            raise ConfigError("M5A forcing contains invalid stellar flux")
        if not 0.0 <= albedo <= 1.0:
            raise ConfigError("bond albedo must be in [0, 1]")
        self.times_s = times_s
        self.toa_fluxes_w_m2 = toa_fluxes_w_m2
        self.albedo = albedo
        self.coverage_s = (times_s[0], times_s[-1])
        self.source_sha256 = sha256_json({
            "kind": self.kind, "source_contract": source_contract,
            "times_s": times_s, "toa_fluxes_w_m2": toa_fluxes_w_m2,
            "bond_albedo": albedo, "global_mean_divisor": 4.0,
            "interpolation": "piecewise_linear_scalar_flux",
        })

    @classmethod
    def from_run(
        cls, run_dir: str | Path, *, bond_albedo: float,
        max_sample_interval_s: float,
    ) -> M5AOrbitForcing:
        reader = SegmentedTrajectoryReader(run_dir)
        if reader.manifest.task.status != "COMPLETED":
            raise ConfigError("dynamic environment forcing requires a COMPLETED M5A run")
        path = Path(run_dir) / "initial_state.json"
        try:
            state = WorldInitialState.model_validate_json(
                path.read_text(encoding="utf-8"), strict=True
            )
        except (OSError, ValueError) as exc:
            raise ConfigError(f"cannot read M5A initial state: {exc}") from exc
        if sha256_json(state) != reader.manifest.initial_state_sha256:
            raise ConfigError("M5A initial-state hash mismatch")
        bodies = {body.id: body for body in state.bodies}
        if tuple(body.id for body in state.bodies) != (
            "star-1", "star-2", "star-3", "planet-1"
        ):
            raise ConfigError("M5A forcing requires canonical four-body order")
        luminosities = []
        for star_id in ("star-1", "star-2", "star-3"):
            value = bodies[star_id].properties.get("luminosity_w")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigError(f"{star_id} luminosity_w is unavailable")
            luminosities.append(float(value))
        samples = tuple(reader.iter_samples())
        if len(samples) < 2:
            raise ConfigError("M5A forcing requires at least two trajectory samples")
        gaps = [
            b.t_s - a.t_s for a, b in zip(samples, samples[1:], strict=False)
        ]
        if max(gaps) > max_sample_interval_s and not math.isclose(
            max(gaps), max_sample_interval_s, rel_tol=1e-12, abs_tol=1e-6
        ):
            raise ConfigError("M5A orbit sample gap exceeds environment configuration")
        fluxes = []
        for sample in samples:
            xyz = {body.id: np.asarray(body.position_m, dtype=float) for body in sample.bodies}
            planet = xyz["planet-1"]
            total = 0.0
            for star_id, luminosity in zip(
                ("star-1", "star-2", "star-3"), luminosities, strict=True
            ):
                distance = float(np.linalg.norm(xyz[star_id] - planet))
                if not math.isfinite(distance) or distance <= 0.0:
                    raise ConfigError("invalid star-planet distance in M5A forcing")
                total += luminosity / (4.0 * math.pi * distance * distance)
            fluxes.append(total)
        contract = {
            "run_id": reader.manifest.run_id,
            "world_id": reader.manifest.world_id,
            "initial_state_sha256": reader.manifest.initial_state_sha256,
            "physics_sha256": reader.manifest.hashes.physics_sha256,
            "schedule_sha256": reader.manifest.hashes.schedule_sha256,
            "segments": [entry.model_dump(mode="json") for entry in reader.manifest.segments
                         if entry.status in {"COMMITTED", "TERMINAL"}],
        }
        return cls(tuple(s.t_s for s in samples), tuple(fluxes),
                   albedo=bond_albedo, source_contract=contract)

    def sample(self, t_s: float) -> EnvironmentForcingValue:
        if not math.isfinite(t_s) or t_s < self.times_s[0] or t_s > self.times_s[-1]:
            raise ConfigError("environment forcing time outside verified M5A coverage")
        right = bisect.bisect_left(self.times_s, t_s)
        if right < len(self.times_s) and self.times_s[right] == t_s:
            toa = self.toa_fluxes_w_m2[right]
            interpolation = "EXACT_SAMPLE"
            bracket = (t_s, t_s)
        else:
            left = right - 1
            fraction = (t_s - self.times_s[left]) / (
                self.times_s[right] - self.times_s[left]
            )
            toa = self.toa_fluxes_w_m2[left] + fraction * (
                self.toa_fluxes_w_m2[right] - self.toa_fluxes_w_m2[left]
            )
            interpolation = "LINEAR_FLUX"
            bracket = (self.times_s[left], self.times_s[right])
        return EnvironmentForcingValue(
            t_s=t_s, top_of_atmosphere_flux_w_m2=toa,
            absorbed_flux_w_m2=(1.0 - self.albedo) * toa / 4.0,
            interpolation=interpolation,
            source_bracket_s=bracket,
        )
