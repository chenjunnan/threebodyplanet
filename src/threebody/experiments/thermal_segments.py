"""M6.1: provenance-bound resumable C0/C1 temperature calculation.

Every checkpoint contains *computed*, rather than extrapolated, thermal states and
integrated budgets. Segments overlap at one verified M5A output timestamp.
This is a solver restart at a sample boundary, not a restart of an internal BDF
multistep state: comparisons to monolithic BDF use explicit numerical tolerances.
"""
from __future__ import annotations

import math
from dataclasses import asdict
from pathlib import Path

import numpy as np

from threebody.common.json import sha256_json, write_json_atomic
from threebody.errors import ConfigError
from threebody.physics.stellar.radiation import latitude_band_means_vectorized
from threebody.physics.thermal.c0 import C0Result, C0Sample, solve_c0
from threebody.physics.thermal.c1 import C1Result, C1Sample, solve_c1
from threebody.physics.thermal.diagnostics import EnergyBudget
from threebody.physics.thermal.models import ThermalModelSettings

CHECKPOINT_SCHEMA = "m6_thermal_checkpoint_v1"
CHUNK_OUTPUT_SAMPLES = 128


def _read_checkpoint(path: Path) -> dict:
    if path.is_symlink():
        raise ConfigError("thermal checkpoint symlink forbidden")
    import json

    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"invalid M6 thermal checkpoint: {path.name}") from exc
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "sha256"}:
        raise ConfigError("invalid thermal checkpoint envelope")
    if sha256_json(envelope["payload"]) != envelope["sha256"]:
        raise ConfigError("thermal checkpoint digest mismatch")
    return envelope


def _energy_budget(parts: list[EnergyBudget], capacity: float, initial_k: float,
                   final_k: float) -> EnergyBudget:
    fields = (
        "absorbed_stellar_energy_j_m2", "internal_heat_energy_j_m2",
        "emitted_thermal_energy_j_m2", "meridional_transport_energy_j_m2",
    )
    values = {name: math.fsum(getattr(budget, name) for budget in parts) for name in fields}
    change = capacity * (final_k - initial_k)
    residual = change - (
        values["absorbed_stellar_energy_j_m2"]
        + values["internal_heat_energy_j_m2"]
        - values["emitted_thermal_energy_j_m2"]
        + values["meridional_transport_energy_j_m2"]
    )
    scale = max(
        capacity * abs(initial_k), capacity * abs(final_k), abs(change),
        sum(abs(value) for value in values.values()), 1.0,
    )
    return EnergyBudget(
        heat_content_change_j_m2=change,
        residual_j_m2=residual,
        normalized_residual=abs(residual) / scale,
        **values,
    )


def solve_resumable_thermal(
    *,
    times_s: list[float],
    physics: ThermalModelSettings,
    flux_at,
    axis_inertial_unit: tuple[float, float, float],
    checkpoint_dir: Path,
    source_contract: dict,
    thermal_settings_sha256: str,
    chunk_output_samples: int = CHUNK_OUTPUT_SAMPLES,
) -> tuple[C0Result | None, C1Result | None, dict]:
    """Persist verified chunks; resume only if *all* scientific inputs match.

    No M5A data or checkpoint is mutated by this function. An interrupted job
    reuses completed chunks, and newly committed chunks are atomic JSON files.
    """
    if not 2 <= chunk_output_samples <= 4096:
        raise ConfigError("M6 chunk_output_samples must be in 2..4096")
    if len(times_s) < 2 or any(
        not math.isfinite(t) for t in times_s
    ) or any(b <= a for a, b in zip(times_s, times_s[1:], strict=False)):
        raise ConfigError("M6 thermal times must be finite, strictly increasing")
    manifest = {
        "schema_version": CHECKPOINT_SCHEMA,
        "source_contract": source_contract,
        "thermal_settings_sha256": thermal_settings_sha256,
        "times_sha256": sha256_json(times_s),
        "axis_inertial_unit": list(axis_inertial_unit),
        "chunk_output_samples": chunk_output_samples,
        "mode": physics.mode,
    }
    if checkpoint_dir.is_symlink():
        raise ConfigError("thermal checkpoint directory symlink forbidden")
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = checkpoint_dir / "checkpoint_manifest.json"
    if manifest_path.exists():
        if _read_checkpoint(manifest_path)["payload"] != manifest:
            raise ConfigError("thermal checkpoint provenance/settings mismatch")
    else:
        # An uninitialized directory with chunks is not a valid checkpoint.
        if any(checkpoint_dir.iterdir()):
            raise ConfigError("thermal checkpoint metadata missing")
        write_json_atomic(manifest_path, {
            "payload": manifest, "sha256": sha256_json(manifest)
        })
    all_c0: list[C0Sample] = []
    all_c1: list[C1Sample] = []
    budgets_c0: list[EnergyBudget] = []
    budgets_c1: list[EnergyBudget] = []
    evaluations_c0 = evaluations_c1 = 0
    max_transport_imbalance = 0.0
    previous_sha = sha256_json(manifest)
    reused = computed = 0
    step = chunk_output_samples - 1
    for number, start in enumerate(range(0, len(times_s) - 1, step)):
        end = min(start + step, len(times_s) - 1)
        target = checkpoint_dir / f"{number:08d}.json"
        if target.exists() or target.is_symlink():
            envelope = _read_checkpoint(target)
            payload = envelope["payload"]
            if (payload.get("index") != number
                or payload.get("start_index") != start
                or payload.get("end_index") != end
                or payload.get("t_start_s") != times_s[start]
                or payload.get("t_final_s") != times_s[end]
                or payload.get("previous_sha256") != previous_sha):
                raise ConfigError("thermal checkpoint chain/coverage mismatch")
            reused += 1
        else:
            current_times = times_s[start:end+1]
            c0 = c1 = None
            if physics.mode in {"both", "c0"}:
                c0_initial = (
                    physics.initial_temperature_k
                    if not all_c0 else all_c0[-1].temperature_k
                )
                c0 = solve_c0(
                    times_s=current_times, initial_temperature_k=c0_initial,
                    planet_center_flux=lambda t: flux_at(t).planet_center_flux_w_m2,
                    bond_albedo=physics.bond_albedo,
                    effective_emissivity=physics.effective_emissivity,
                    areal_heat_capacity_j_m2_k=physics.areal_heat_capacity_j_m2_k,
                    internal_heat_flux_w_m2=physics.internal_heat_flux_w_m2,
                    integrator=physics.integrator,
                )
            if physics.mode in {"both", "c1"}:
                c1_initial = (
                    physics.initial_temperature_k
                    if not all_c1 else all_c1[-1].temperature_k
                )
                c1 = solve_c1(
                    times_s=current_times, initial_temperature_k=c1_initial,
                    zonal_incoming=lambda t, _lat: latitude_band_means_vectorized(
                        flux_at(t), physics.c1.latitude_bands, axis_inertial_unit
                    ),
                    latitude_bands=physics.c1.latitude_bands,
                    bond_albedo=physics.bond_albedo,
                    effective_emissivity=physics.effective_emissivity,
                    areal_heat_capacity_j_m2_k=physics.areal_heat_capacity_j_m2_k,
                    internal_heat_flux_w_m2=physics.internal_heat_flux_w_m2,
                    diffusivity_w_m2_k=physics.c1.diffusivity_w_m2_k,
                    integrator=physics.integrator,
                )
            payload = {
                "index": number, "start_index": start, "end_index": end,
                "t_start_s": times_s[start], "t_final_s": times_s[end],
                "previous_sha256": previous_sha,
                "c0": None if c0 is None else {
                    "samples": [asdict(p) for p in c0.samples],
                    "energy_budget": asdict(c0.energy_budget),
                    "function_evaluations": c0.function_evaluations,
                },
                "c1": None if c1 is None else {
                    "samples": [asdict(p) for p in c1.samples],
                    "energy_budget": asdict(c1.energy_budget),
                    "function_evaluations": c1.function_evaluations,
                    "maximum_absolute_global_transport_imbalance_w_m2":
                        c1.maximum_absolute_global_transport_imbalance_w_m2,
                },
            }
            envelope = {"payload": payload, "sha256": sha256_json(payload)}
            write_json_atomic(target, envelope)
            computed += 1
        for mode, cls, aggregate, budgets in (
            ("c0", C0Sample, all_c0, budgets_c0),
            ("c1", C1Sample, all_c1, budgets_c1),
        ):
            section = payload[mode]
            if (section is None) != (physics.mode not in {"both", mode}):
                raise ConfigError("thermal checkpoint mode mismatch")
            if section is None:
                continue
            samples = [cls(**entry) for entry in section["samples"]]
            if (len(samples) != end-start+1
                or [p.t_s for p in samples] != times_s[start:end+1]):
                raise ConfigError("thermal checkpoint sample grid mismatch")
            if aggregate:
                if not np.array_equal(
                    np.asarray(samples[0].temperature_k),
                    np.asarray(aggregate[-1].temperature_k),
                ):
                    raise ConfigError("thermal checkpoint temperature continuity violated")
                aggregate.extend(samples[1:])
            else:
                aggregate.extend(samples)
            budgets.append(EnergyBudget(**section["energy_budget"]))
        if payload["c0"] is not None:
            evaluations_c0 += payload["c0"]["function_evaluations"]
        if payload["c1"] is not None:
            evaluations_c1 += payload["c1"]["function_evaluations"]
            max_transport_imbalance = max(
                max_transport_imbalance,
                payload["c1"]["maximum_absolute_global_transport_imbalance_w_m2"],
            )
        previous_sha = envelope["sha256"]
    c0_result = c1_result = None
    cap = physics.areal_heat_capacity_j_m2_k
    if all_c0:
        c0_result = C0Result(
            samples=tuple(all_c0), solver="scipy.solve_ivp.RK45",
            function_evaluations=evaluations_c0,
            energy_budget=_energy_budget(
                budgets_c0, cap, all_c0[0].temperature_k,
                all_c0[-1].temperature_k,
            ),
        )
    if all_c1:
        c1_result = C1Result(
            samples=tuple(all_c1), solver="scipy.solve_ivp.BDF",
            function_evaluations=evaluations_c1,
            energy_budget=_energy_budget(
                budgets_c1, cap, all_c1[0].global_mean_temperature_k,
                all_c1[-1].global_mean_temperature_k,
            ),
            maximum_absolute_global_transport_imbalance_w_m2=max_transport_imbalance,
        )
    if (c0_result is not None and len(c0_result.samples) != len(times_s)) or (
        c1_result is not None and len(c1_result.samples) != len(times_s)
    ):
        raise ConfigError("M6 checkpoint reconstructed unexpected sample count")
    return c0_result, c1_result, {
        "checkpoint_schema": CHECKPOINT_SCHEMA,
        "chunk_count": reused + computed,
        "reused_chunks": reused,
        "computed_chunks": computed,
        "chunk_output_samples": chunk_output_samples,
        "c0_function_evaluations": evaluations_c0,
        "c1_function_evaluations": evaluations_c1,
        "checkpoint_chain_sha256": previous_sha,
        "restart_semantics": "restart_at_verified_output_timestamp_not_internal_BDF_state",
    }
