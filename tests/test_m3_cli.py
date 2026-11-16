from __future__ import annotations

import json
from pathlib import Path

from threebody.cli import build_parser, execute
from threebody.common.json import write_json_atomic
from threebody.errors import ThermalExecutionError
from threebody.experiments.simulation import run_simulation_archive
from threebody.experiments.thermal import run_thermal_archive
from threebody.world.generators import generate_initial_state


def _m3_inputs(tmp_path, resolved, *, mode="both"):
    config, digest = resolved
    state = generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )
    initial = tmp_path / "candidate.initial.json"
    write_json_atomic(initial, state)
    m2_text = Path("configs/m2_ias15.yaml").read_text(encoding="utf-8")
    m2_text = m2_text.replace("duration_s: 31557600.0", "duration_s: 1000.0")
    m2_text = m2_text.replace("output_interval_s: 86400.0", "output_interval_s: 500.0")
    m2_config = tmp_path / "m2.yaml"
    m2_config.write_text(m2_text, encoding="utf-8")
    m2_archive = tmp_path / "m2"
    assert run_simulation_archive(initial, m2_config, m2_archive).status == "COMPLETED"
    m3_config = tmp_path / "m3.yaml"
    m3_config.write_text(
        f"""
schema_version: "0.3.0"
input:
  initial_state: {initial.name}
  m2_archive: {m2_archive.name}
  allow_collision_terminated: false
  max_orbit_sample_interval_s: 500.0
planet:
  id: planet-1
  rotation:
    axis_inertial_unit: [0.0, 0.0, 1.0]
    prime_meridian_inertial_unit: [1.0, 0.0, 0.0]
    rotation_epoch_s: 0.0
    initial_phase_rad: 0.0
    rotation_rate_rad_s: 7.2921159e-5
radiation: {{model: isotropic_bolometric_v1, occultation: disabled}}
thermal:
  mode: {mode}
  initial_temperature_k: 288.0
  bond_albedo: 0.3
  effective_emissivity: 1.0
  areal_heat_capacity_j_m2_k: 4.0e+7
  internal_heat_flux_w_m2: 0.0
  c1: {{latitude_bands: 8, diffusivity_w_m2_k: 0.6}}
  integrator: {{rtol: 1.0e-7, atol_k: 1.0e-5, max_step_s: 100.0}}
output: {{sample_interval_s: 500.0, max_samples: 10}}
""",
        encoding="utf-8",
    )
    return m3_config


def test_world_thermal_cli_writes_traceable_atomic_archive(tmp_path, resolved):
    config = _m3_inputs(tmp_path, resolved)
    output = tmp_path / "thermal"
    args = build_parser().parse_args(
        ["world", "thermal", "--config", str(config), "--out-dir", str(output)]
    )
    assert execute(args) == 0
    expected = {
        "resolved_thermal_config.json",
        "thermal_manifest.json",
        "stellar_flux.jsonl",
        "c0_temperature.jsonl",
        "c1_latitude_temperature.jsonl",
        "energy_budget.json",
    }
    assert {path.name for path in output.iterdir()} == expected
    manifest = json.loads((output / "thermal_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "COMPLETED"
    assert manifest["sample_count"] == 3
    assert manifest["orbit_interpolation"]["algorithm"].startswith("piecewise_cubic")
    budget = json.loads((output / "energy_budget.json").read_text(encoding="utf-8"))
    assert budget["c0"]["normalized_residual"] < 1.0e-8
    assert budget["c1"]["normalized_residual"] < 1.0e-8
    for path in output.iterdir():
        text = path.read_text(encoding="utf-8")
        assert "NaN" not in text and "Infinity" not in text


def test_thermal_solver_failure_is_archived_as_failed(
    tmp_path, resolved, monkeypatch
):
    config = _m3_inputs(tmp_path, resolved, mode="c0")

    def fail(**_kwargs):
        raise ThermalExecutionError("synthetic thermal failure")

    monkeypatch.setattr("threebody.experiments.thermal.solve_c0", fail)
    output = tmp_path / "failed-thermal"
    manifest = run_thermal_archive(config, output)
    assert manifest["status"] == "FAILED"
    assert "synthetic thermal failure" in manifest["error"]
    archived = json.loads((output / "thermal_manifest.json").read_text(encoding="utf-8"))
    assert archived["status"] == "FAILED"
    assert (output / "stellar_flux.jsonl").read_text(encoding="utf-8") == ""
