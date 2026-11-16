from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from threebody.common.constants import G
from threebody.errors import ThreeBodyError
from threebody.physics.nbody.diagnostics import DiagnosticTracker, compute_diagnostics
from threebody.physics.nbody.events import EventObserver
from threebody.physics.nbody.guard import ReboundInputGuard
from threebody.physics.nbody.models import (
    BodyTrajectoryState,
    DiagnosticSummary,
    IntegrationSettings,
    SimulationEvent,
    TrajectorySample,
)
from threebody.world.models import WorldInitialState


class ReboundUnavailableError(ThreeBodyError):
    """The optional REBOUND dependency is not installed."""


class IntegrationExecutionError(ThreeBodyError):
    """REBOUND failed before producing a scientifically valid result."""


@dataclass(frozen=True)
class IntegrationRunData:
    status: str
    t_final_s: float
    sample_count: int
    events: tuple[SimulationEvent, ...]
    minimum_distances_m: dict[str, float]
    diagnostic_summary: DiagnosticSummary
    rebound_version: str
    error: str | None = None


def _import_rebound():
    try:
        import rebound
    except ImportError as exc:
        raise ReboundUnavailableError(
            "REBOUND is required; install with `pip install -e '.[physics]'`"
        ) from exc
    return rebound


def _arrays(simulation) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    particles = simulation.particles
    masses = np.asarray([particle.m for particle in particles], dtype=np.float64)
    positions = np.asarray(
        [[particle.x, particle.y, particle.z] for particle in particles], dtype=np.float64
    )
    velocities = np.asarray(
        [[particle.vx, particle.vy, particle.vz] for particle in particles], dtype=np.float64
    )
    return masses, positions, velocities


def _sample(simulation, body_ids: tuple[str, ...]) -> TrajectorySample:
    masses, positions, velocities = _arrays(simulation)
    diagnostics = compute_diagnostics(float(simulation.t), masses, positions, velocities)
    bodies = tuple(
        BodyTrajectoryState(
            id=body_id,
            position_m=tuple(float(value) for value in positions[index]),
            velocity_mps=tuple(float(value) for value in velocities[index]),
        )
        for index, body_id in enumerate(body_ids)
    )
    return TrajectorySample(t_s=float(simulation.t), bodies=bodies, diagnostics=diagnostics)


def _build_simulation(initial_state: WorldInitialState, settings: IntegrationSettings):
    rebound = _import_rebound()
    simulation = rebound.Simulation()
    simulation.G = G
    simulation.integrator = "ias15"
    simulation.ri_ias15.epsilon = settings.epsilon
    simulation.softening = settings.softening_m
    simulation.t = settings.start_time_s
    for body in initial_state.bodies:
        simulation.add(
            m=body.mass_kg,
            r=body.radius_m,
            x=body.position_m[0],
            y=body.position_m[1],
            z=body.position_m[2],
            vx=body.velocity_mps[0],
            vy=body.velocity_mps[1],
            vz=body.velocity_mps[2],
            hash=body.id,
        )
    # LINE includes a linearized swept-path check over the last internal step and
    # is less likely than endpoint-only DIRECT to miss a high-speed crossing.
    simulation.collision = "line"
    simulation.collision_resolve = "halt"
    return rebound, simulation


def integrate_stream(
    initial_state: WorldInitialState,
    settings: IntegrationSettings,
    on_sample: Callable[[TrajectorySample], None],
) -> IntegrationRunData:
    ReboundInputGuard().validate(initial_state, settings)
    rebound, simulation = _build_simulation(initial_state, settings)
    body_ids = tuple(body.id for body in initial_state.bodies)
    radii = tuple(body.radius_m for body in initial_state.bodies)
    observer = EventObserver(body_ids=body_ids, radii_m=radii, settings=settings)
    simulation.heartbeat = observer.heartbeat
    observer.observe(simulation, "initial_state")

    initial_sample = _sample(simulation, body_ids)
    tracker = DiagnosticTracker(initial_sample.diagnostics)
    on_sample(initial_sample)
    sample_count = 1
    last_sample_time = initial_sample.t_s
    status = "COMPLETED"
    error = None
    output_index = 1

    while last_sample_time < settings.end_time_s:
        target = min(
            settings.start_time_s + output_index * settings.output_interval_s,
            settings.end_time_s,
        )
        try:
            simulation.integrate(target, exact_finish_time=1)
        except rebound.Collision:
            status = "COLLISION"
            observer.observe(simulation, "collision_halt_state")
            observer.collision_event(simulation)
        except Exception as exc:
            raise IntegrationExecutionError(
                f"REBOUND IAS15 failed at t={float(simulation.t)} s: {exc}"
            ) from exc

        if observer.timed_out:
            status = "STOPPED"
            error = "max_wall_seconds reached"

        current_time = float(simulation.t)
        if current_time > last_sample_time:
            sample = _sample(simulation, body_ids)
            tracker.update(sample.diagnostics)
            on_sample(sample)
            sample_count += 1
            last_sample_time = current_time
        if status != "COMPLETED":
            break
        if current_time != target:
            raise IntegrationExecutionError(
                f"IAS15 did not finish at requested output time {target}; got {current_time}"
            )
        output_index += 1

    summary = tracker.summary(conservation_applicable=status != "COLLISION")
    return IntegrationRunData(
        status=status,
        t_final_s=float(simulation.t),
        sample_count=sample_count,
        events=tuple(observer.events),
        minimum_distances_m=dict(observer.minimum_distances_m),
        diagnostic_summary=summary,
        rebound_version=rebound.__version__,
        error=error,
    )


class ReboundIAS15Integrator:
    """Convenience in-memory facade for bounded tests and small integrations."""

    def integrate(
        self, initial_state: WorldInitialState, settings: IntegrationSettings
    ) -> tuple[IntegrationRunData, tuple[TrajectorySample, ...]]:
        samples: list[TrajectorySample] = []
        run = integrate_stream(initial_state, settings, samples.append)
        return run, tuple(samples)

