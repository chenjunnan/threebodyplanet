from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from threebody.cli import build_parser, execute
from threebody.common.json import write_json_atomic
from threebody.errors import ConfigError
from threebody.experiments.assessment import run_world_assessment
from threebody.experiments.simulation import run_simulation_archive
from threebody.experiments.thermal import run_thermal_archive
from threebody.physics.nbody.models import SimulationEvent
from threebody.physics.outcomes.adapter import M4Inputs, load_m4_inputs
from threebody.physics.outcomes.config import load_m4_settings
from threebody.physics.outcomes.engine import assess_world
from threebody.physics.outcomes.models import C0Point, C1Point, FluxPoint, StarFluxPoint
from threebody.world.generators import generate_initial_state


def _flux(t_s: float, value: float) -> FluxPoint:
    stars = tuple(
        StarFluxPoint(
            body_id=f"star-{index}",
            luminosity_w=1.0e26,
            distance_m=1.0e11,
            direction_inertial_unit=(1.0, 0.0, 0.0),
            flux_w_m2=value / 3.0,
        )
        for index in range(1, 4)
    )
    return FluxPoint(
        t_s=t_s,
        planet_id="planet-1",
        stars=stars,
        planet_center_flux_w_m2=value,
        globally_averaged_incoming_w_m2=value / 4.0,
        model="isotropic_bolometric_v1",
        occultation="disabled",
        reference_equator_prime_meridian_normal=(1.0, 0.0, 0.0),
        reference_equator_prime_meridian_toa_w_m2=value,
    )


def _c0(t_s: float, temperature: float) -> C0Point:
    return C0Point(
        t_s=t_s,
        temperature_k=temperature,
        planet_center_flux_w_m2=1000.0,
        globally_averaged_incoming_w_m2=250.0,
        absorbed_w_m2=175.0,
        internal_heat_flux_w_m2=0.0,
        emitted_w_m2=175.0,
        net_heating_w_m2=0.0,
    )


def _c1(t_s: float, temperatures: tuple[float, ...]) -> C1Point:
    count = len(temperatures)
    zeros = (0.0,) * count
    return C1Point(
        t_s=t_s,
        latitude_center_rad=tuple(-1.0 + 2.0 * i / (count - 1) for i in range(count)),
        temperature_k=temperatures,
        zonal_incoming_w_m2=zeros,
        absorbed_w_m2=zeros,
        emitted_w_m2=zeros,
        meridional_convergence_w_m2=zeros,
        global_mean_temperature_k=sum(temperatures) / count,
        globally_averaged_incoming_w_m2=0.0,
    )


def _synthetic_inputs(**updates):
    values = {
        "initial_path": Path("initial.json"),
        "initial_state": SimpleNamespace(world_id="candidate-000000"),
        "initial_state_sha256": "a" * 64,
        "m2_archive": None,
        "m2_manifest_sha256": None,
        "m3_dir": Path("m3"),
        "m3_manifest": {"status": "COMPLETED"},
        "m3_manifest_sha256": "b" * 64,
        "flux": (_flux(0.0, 1000.0), _flux(10.0, 2000.0), _flux(20.0, 1000.0)),
        "c0": (_c0(0.0, 200.0), _c0(10.0, 280.0), _c0(20.0, 350.0)),
        "c1": (
            _c1(0.0, (220.0, 250.0, 340.0, 300.0)),
            _c1(20.0, (230.0, 260.0, 350.0, 310.0)),
        ),
        "energy_budget": {},
        "initial_outcomes": (),
    }
    values.update(updates)
    return M4Inputs(**values)


def _settings():
    settings, _ = load_m4_settings("configs/m4_world_outcomes.yaml")
    classification = settings.classification.model_copy(
        update={
            "cold_threshold_k": 240.0,
            "hot_threshold_k": 330.0,
            "persistence_min_s": 2.0,
        }
    )
    return settings.model_copy(update={"classification": classification})


def test_piecewise_threshold_durations_and_sampled_statistics_are_auditable():
    statistics, episodes, events, classification = assess_world(
        _synthetic_inputs(), _settings()
    )
    assert statistics.cold_duration_s == pytest.approx(5.0)
    assert statistics.hot_duration_s == pytest.approx(20.0 - (10.0 + 50.0 / 7.0))
    assert statistics.integrated_global_incoming_energy_j_m2 == pytest.approx(7500.0)
    assert statistics.maximum_c1_cold_band_fraction == 0.25
    assert statistics.maximum_c1_hot_band_fraction == 0.25
    assert {episode.kind for episode in episodes} == {"COLD", "HOT"}
    assert {event.event_type for event in events} >= {
        "COLD_EXPOSURE",
        "HOT_EXPOSURE",
        "RAPID_IRRADIANCE_CHANGE",
    }
    assert classification.environmental_outcomes == (
        "FROZEN_EXPOSURE",
        "HEAT_EXPOSURE",
        "HIGH_IRRADIANCE_VARIABILITY",
    )


@pytest.mark.parametrize(
    ("body_ids", "expected_planet", "expected_stellar"),
    [(('star-1', 'star-2'), False, True), (('star-1', 'planet-1'), True, False)],
)
def test_collision_classification_separates_stellar_and_planet_events(
    body_ids, expected_planet, expected_stellar
):
    collision = SimulationEvent(
        event_type="COLLISION",
        t_s=5.0,
        body_ids=body_ids,
        observed={},
        message="collision",
        detection_method="rebound_line_collision_callback",
    )
    archive = SimpleNamespace(
        events=(collision,),
        manifest=SimpleNamespace(status="COLLISION"),
        samples=(SimpleNamespace(t_s=0.0), SimpleNamespace(t_s=5.0)),
    )
    _, _, timeline, classification = assess_world(
        _synthetic_inputs(m2_archive=archive), _settings()
    )
    assert classification.collision_with_planet is expected_planet
    assert classification.stellar_collision is expected_stellar
    assert classification.dynamical_outcome == "COLLISION_TERMINATED"
    assert any(event.terminal for event in timeline)
    if expected_stellar:
        assert classification.planet_status == "unresolved"


def test_escape_candidate_does_not_terminate_or_claim_permanent_escape():
    escape = SimulationEvent(
        event_type="ESCAPE_CANDIDATE",
        t_s=5.0,
        body_ids=("planet-1",),
        observed={},
        message="candidate only",
        detection_method="heuristic",
    )
    archive = SimpleNamespace(
        events=(escape,),
        manifest=SimpleNamespace(status="COMPLETED"),
        samples=(SimpleNamespace(t_s=0.0), SimpleNamespace(t_s=20.0)),
    )
    _, _, timeline, classification = assess_world(
        _synthetic_inputs(m2_archive=archive), _settings()
    )
    assert classification.dynamical_outcome == "ESCAPE_CANDIDATE"
    assert classification.ejection_candidate
    assert not next(event for event in timeline if event.source == "M2").terminal
    assert any("not proof of permanent escape" in text for text in classification.narrative_limits)


def test_initial_contact_without_m2_or_m3_is_a_valid_incomplete_report(tmp_path, resolved):
    config, digest = resolved
    state = generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )
    initial = tmp_path / "initial.json"
    write_json_atomic(initial, state)
    outcome = tmp_path / "initial_outcome.json"
    write_json_atomic(
        outcome,
        [
            {
                "schema_version": "0.4.0",
                "world_id": state.world_id,
                "status": "INITIAL_CONTACT",
                "event_type": "INITIAL_COLLISION",
                "t_s": 0.0,
                "terminal": True,
                "integration_performed": False,
                "body_ids": ["star-1", "planet-1"],
                "center_distance_m": 1.0,
                "contact_distance_m": 2.0,
                "surface_gap_m": -1.0,
                "detection_method": "initial_sphere_geometry",
            }
        ],
    )
    output = tmp_path / "assessment"
    summary = run_world_assessment(
        initial_path=initial,
        initial_outcome_path=outcome,
        config_path="configs/m4_world_outcomes.yaml",
        out_dir=output,
    )
    assert summary["scientific_input_status"] == "INITIAL_CONTACT"
    assert summary["dynamical_outcome"] == "INITIAL_CONTACT"
    assert summary["environmental_outcomes"] == ["DATA_INSUFFICIENT"]
    timeline = (output / "world_timeline.jsonl").read_text(encoding="utf-8")
    assert "INITIAL_CONTACT" in timeline


def _real_archives(tmp_path, resolved):
    config, digest = resolved
    state = generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )
    initial = tmp_path / "candidate.initial.json"
    write_json_atomic(initial, state)
    m2_config = tmp_path / "m2.yaml"
    m2_config.write_text(
        """schema_version: \"0.2.0\"
backend: rebound_ias15
start_time_s: 0.0
duration_s: 1000.0
output_interval_s: 500.0
epsilon: 1.0e-9
collision: {mode: terminate}
encounter: {enabled: true, threshold_mode: radius_multiple, radius_multiplier: 5.0}
escape:
  enabled: false
  boundary_radius_m: 1.0e+14
  require_outward_velocity: true
  require_positive_specific_energy: true
  minimum_duration_s: 0.0
require_positive_radius: false
max_samples: 10
max_wall_seconds: 30.0
softening_m: 0.0
""",
        encoding="utf-8",
    )
    m2 = tmp_path / "m2"
    assert run_simulation_archive(initial, m2_config, m2).status == "COMPLETED"
    m3_config = tmp_path / "m3.yaml"
    m3_config.write_text(
        f"""schema_version: \"0.3.0\"
input:
  initial_state: {initial}
  m2_archive: {m2}
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
  mode: both
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
    m3 = tmp_path / "m3"
    assert run_thermal_archive(m3_config, m3)["status"] == "COMPLETED"
    return initial, m2, m3


def test_real_m2_m3_assessment_cli_and_provenance_validation(tmp_path, resolved):
    initial, m2, m3 = _real_archives(tmp_path, resolved)
    parser = build_parser()
    output = tmp_path / "m4"
    status = execute(
        parser.parse_args(
            [
                "world",
                "assess",
                "--initial",
                str(initial),
                "--m2-archive",
                str(m2),
                "--m3-archive",
                str(m3),
                "--config",
                "configs/m4_world_outcomes.yaml",
                "--out-dir",
                str(output),
            ]
        )
    )
    assert status == 0
    expected = {
        "world_outcome_manifest.json",
        "environment_events.json",
        "environment_statistics.json",
        "world_classification.json",
        "world_timeline.jsonl",
        "world_assessment_summary.json",
        "resolved_m4_config.json",
    }
    assert {path.name for path in output.iterdir()} == expected
    manifest = json.loads((output / "world_outcome_manifest.json").read_text())
    assert manifest["source_hashes"]["m2_manifest_sha256"]
    assert manifest["source_hashes"]["m3_manifest_sha256"]

    thermal_manifest = m3 / "thermal_manifest.json"
    payload = json.loads(thermal_manifest.read_text())
    payload["world_id"] = "unrelated-world"
    write_json_atomic(thermal_manifest, payload)
    with pytest.raises(ConfigError, match="world_id"):
        load_m4_inputs(initial, m2, m3, None)
