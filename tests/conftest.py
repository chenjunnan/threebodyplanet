from __future__ import annotations

from pathlib import Path

import pytest

from threebody.common.json import write_json_atomic
from threebody.config.loader import load_and_resolve
from threebody.physics.nbody.models import (
    BodyTrajectoryState,
    DiagnosticSnapshot,
    EncounterSettings,
    EscapeSettings,
    IntegrationResult,
    IntegrationSettings,
    SimulationEvent,
    TrajectorySample,
)


@pytest.fixture
def example_path() -> Path:
    return Path(__file__).parents[1] / "configs" / "example_experiment.yaml"


@pytest.fixture
def resolved(example_path):
    return load_and_resolve(example_path)


@pytest.fixture
def m2_viz_archive_factory(tmp_path):
    def create(
        *,
        times=(0.0, 10.0, 20.0),
        status="COMPLETED",
        events=None,
        name="m2-viz",
    ):
        directory = tmp_path / name
        directory.mkdir()
        samples = []
        for t_s in times:
            bodies = tuple(
                BodyTrajectoryState(
                    id=body_id,
                    position_m=(
                        float(index * 1000.0 + (index + 1) * t_s),
                        float(index * 2000.0),
                        float(-index * 500.0),
                    ),
                    velocity_mps=(float(index + 1), 0.0, 0.0),
                )
                for index, body_id in enumerate(
                    ("star-1", "star-2", "star-3", "planet-1")
                )
            )
            diagnostics = DiagnosticSnapshot(
                t_s=float(t_s),
                kinetic_energy_j=1.0,
                potential_energy_j=-2.0,
                total_energy_j=-1.0,
                energy_scale_j=3.0,
                linear_momentum_kg_mps=(0.0, 0.0, 0.0),
                angular_momentum_kg_m2_s=(0.0, 0.0, 1.0),
                barycenter_position_m=(0.0, 0.0, 0.0),
                barycenter_velocity_mps=(0.0, 0.0, 0.0),
            )
            samples.append(
                TrajectorySample(t_s=float(t_s), bodies=bodies, diagnostics=diagnostics)
            )
        if events is None:
            events = (
                SimulationEvent(
                    event_type="CLOSE_ENCOUNTER",
                    t_s=5.0 if len(times) > 1 else float(times[0]),
                    body_ids=("star-1", "planet-1"),
                    observed={"observed_distance_m": 1.0},
                    message="display estimate only",
                    detection_method="test_internal_step",
                ),
            )
        (directory / "trajectory.jsonl").write_text(
            "".join(sample.model_dump_json() + "\n" for sample in samples),
            encoding="utf-8",
        )
        write_json_atomic(
            directory / "events.json",
            [event.model_dump(mode="json") for event in events],
        )
        settings = IntegrationSettings(
            start_time_s=float(times[0]),
            duration_s=max(float(times[-1] - times[0]), 1.0),
            output_interval_s=max(float(times[-1] - times[0]), 1.0),
            encounter=EncounterSettings(enabled=False),
            escape=EscapeSettings(enabled=False, boundary_radius_m=1.0e20),
        )
        manifest = IntegrationResult(
            world_id="viz-test",
            initial_state_sha256="0" * 64,
            settings_sha256="1" * 64,
            integrator_version="test",
            project_version="0.2.0",
            python_version="3.11",
            numpy_version="test",
            constant_set_version="test",
            generated_at_utc="2026-11-16T00:00:00+00:00",
            settings=settings,
            status=status,
            t_start_s=float(times[0]),
            t_final_s=float(times[-1]),
            sample_count=len(samples),
            event_count=len(events),
            artifact_refs={
                "trajectory": "trajectory.jsonl",
                "events": "events.json",
            },
            diagnostic_summary=None,
            error="partial" if status in {"STOPPED", "FAILED"} else None,
        )
        write_json_atomic(directory / "simulation_manifest.json", manifest)
        return directory

    return create

