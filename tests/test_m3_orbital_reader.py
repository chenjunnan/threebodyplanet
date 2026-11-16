from __future__ import annotations

import json

import pytest

from threebody.common.json import sha256_json, write_json_atomic
from threebody.errors import ConfigError
from threebody.experiments.orbital_reader import M2TrajectoryReader
from threebody.physics.nbody.models import (
    BodyTrajectoryState,
    DiagnosticSnapshot,
    EncounterSettings,
    EscapeSettings,
    IntegrationResult,
    IntegrationSettings,
    TrajectorySample,
)
from threebody.physics.thermal.config import load_thermal_settings
from threebody.world.generators import generate_initial_state


def _diagnostics(t_s):
    return DiagnosticSnapshot(
        t_s=t_s,
        kinetic_energy_j=1.0,
        potential_energy_j=-2.0,
        total_energy_j=-1.0,
        energy_scale_j=3.0,
        linear_momentum_kg_mps=(0.0, 0.0, 0.0),
        angular_momentum_kg_m2_s=(0.0, 0.0, 1.0),
        barycenter_position_m=(0.0, 0.0, 0.0),
        barycenter_velocity_mps=(0.0, 0.0, 0.0),
    )


def _write_m2_archive(tmp_path, resolved, *, status="COMPLETED"):
    config, digest = resolved
    initial_state = generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )
    initial_path = tmp_path / "candidate.initial.json"
    write_json_atomic(initial_path, initial_state)
    samples = []
    for t_s in (0.0, 10.0):
        bodies = tuple(
            BodyTrajectoryState(
                id=body.id,
                position_m=tuple(
                    position + velocity * t_s
                    for position, velocity in zip(
                        body.position_m, body.velocity_mps, strict=True
                    )
                ),
                velocity_mps=body.velocity_mps,
            )
            for body in initial_state.bodies
        )
        samples.append(TrajectorySample(t_s=t_s, bodies=bodies, diagnostics=_diagnostics(t_s)))
    archive = tmp_path / "m2"
    archive.mkdir()
    (archive / "trajectory.jsonl").write_text(
        "".join(sample.model_dump_json() + "\n" for sample in samples),
        encoding="utf-8",
    )
    settings = IntegrationSettings(
        start_time_s=0.0,
        duration_s=10.0,
        output_interval_s=10.0,
        encounter=EncounterSettings(enabled=False),
        escape=EscapeSettings(enabled=False, boundary_radius_m=1.0e20),
    )
    manifest = IntegrationResult(
        world_id=initial_state.world_id,
        initial_state_sha256=sha256_json(initial_state),
        settings_sha256="1" * 64,
        integrator_version="test",
        project_version="0.2.0",
        python_version="3.11",
        numpy_version="test",
        constant_set_version="test",
        generated_at_utc="2026-11-16T00:00:00+00:00",
        settings=settings,
        status=status,
        t_start_s=0.0,
        t_final_s=10.0,
        sample_count=2,
        event_count=0,
        artifact_refs={"trajectory": "trajectory.jsonl"},
        diagnostic_summary=None,
    )
    write_json_atomic(archive / "simulation_manifest.json", manifest)
    return initial_path, archive, initial_state


def test_reader_validates_archive_and_hermite_reproduces_nodes_and_velocity(
    tmp_path, resolved
):
    initial_path, archive, initial = _write_m2_archive(tmp_path, resolved)
    reader = M2TrajectoryReader(
        initial_path, archive, max_sample_interval_s=10.0
    )
    assert reader.sample(0.0).bodies[0].position_m == initial.bodies[0].position_m
    assert reader.sample(10.0).bodies[0].velocity_mps == initial.bodies[0].velocity_mps
    midpoint = reader.sample(5.0).bodies[0]
    assert midpoint.position_m == pytest.approx(
        tuple(
            position + velocity * 5.0
            for position, velocity in zip(
                initial.bodies[0].position_m,
                initial.bodies[0].velocity_mps,
                strict=True,
            )
        )
    )
    assert midpoint.velocity_mps == pytest.approx(initial.bodies[0].velocity_mps)
    assert reader.diagnostics.algorithm == "piecewise_cubic_hermite_position_velocity"
    with pytest.raises(ConfigError, match="outside"):
        reader.sample(10.1)


def test_reader_rejects_wrong_hash_interval_and_terminal_status(tmp_path, resolved):
    initial_path, archive, _ = _write_m2_archive(tmp_path, resolved)
    manifest_path = archive / "simulation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["initial_state_sha256"] = "0" * 64
    write_json_atomic(manifest_path, manifest)
    with pytest.raises(ConfigError, match="initial_state_sha256"):
        M2TrajectoryReader(initial_path, archive, max_sample_interval_s=10.0)

    other = tmp_path / "other"
    other.mkdir()
    initial_path, archive, _ = _write_m2_archive(other, resolved, status="STOPPED")
    with pytest.raises(ConfigError, match="STOPPED"):
        M2TrajectoryReader(initial_path, archive, max_sample_interval_s=10.0)

    collision = tmp_path / "collision"
    collision.mkdir()
    initial_path, archive, _ = _write_m2_archive(collision, resolved, status="COLLISION")
    with pytest.raises(ConfigError, match="explicit opt-in"):
        M2TrajectoryReader(initial_path, archive, max_sample_interval_s=10.0)
    assert M2TrajectoryReader(
        initial_path,
        archive,
        allow_collision_terminated=True,
        max_sample_interval_s=10.0,
    ).t_end_s == 10.0
    with pytest.raises(ConfigError, match="exceeds configured maximum"):
        M2TrajectoryReader(
            initial_path,
            archive,
            allow_collision_terminated=True,
            max_sample_interval_s=9.0,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda records: records[1].update({"t_s": 0.0}), "strictly increasing"),
        (
            lambda records: records[1]["bodies"][0].update({"id": "wrong-star"}),
            "body ID order mismatch",
        ),
        (
            lambda records: records[1]["bodies"][0].update(
                {"position_m": [float("nan"), 0.0, 0.0]}
            ),
            "invalid trajectory line",
        ),
    ],
)
def test_reader_rejects_time_id_and_nonfinite_corruption(
    tmp_path, resolved, mutation, message
):
    initial_path, archive, _ = _write_m2_archive(tmp_path, resolved)
    trajectory = archive / "trajectory.jsonl"
    records = [json.loads(line) for line in trajectory.read_text().splitlines()]
    mutation(records)
    trajectory.write_text(
        "".join(json.dumps(record, allow_nan=True) + "\n" for record in records),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=message):
        M2TrajectoryReader(initial_path, archive, max_sample_interval_s=10.0)


def test_thermal_config_is_strict_and_rotation_basis_is_not_silently_normalized(
    tmp_path,
):
    text = """
schema_version: "0.3.0"
input:
  initial_state: initial.json
  m2_archive: m2
  allow_collision_terminated: false
  max_orbit_sample_interval_s: 10.0
planet:
  id: planet-1
  rotation:
    axis_inertial_unit: [0.0, 0.0, 1.0]
    prime_meridian_inertial_unit: [1.0, 0.0, 0.0]
    rotation_epoch_s: 0.0
    initial_phase_rad: 0.0
    rotation_rate_rad_s: 1.0
radiation: {model: isotropic_bolometric_v1, occultation: disabled}
thermal:
  mode: both
  initial_temperature_k: 288.0
  bond_albedo: 0.3
  effective_emissivity: 1.0
  areal_heat_capacity_j_m2_k: 4.0e+7
  internal_heat_flux_w_m2: 0.0
  c1: {latitude_bands: 18, diffusivity_w_m2_k: 0.6}
  integrator: {rtol: 1.0e-7, atol_k: 1.0e-5, max_step_s: 60.0}
output: {sample_interval_s: 10.0, max_samples: 10}
"""
    path = tmp_path / "m3.yaml"
    path.write_text(text, encoding="utf-8")
    settings, digest = load_thermal_settings(path)
    assert settings.schema_version == "0.3.0"
    assert len(digest) == 64
    path.write_text(text.replace("[1.0, 0.0, 0.0]", "[2.0, 0.0, 0.0]"), encoding="utf-8")
    with pytest.raises(ConfigError, match="must already be a unit vector"):
        load_thermal_settings(path)
