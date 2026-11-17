from __future__ import annotations

import math
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from threebody.common.constants import M_EARTH_KG, M_SUN_KG, R_SUN_M, G
from threebody.config.models import AnyGeneratorConfig, GeneratorConfig
from threebody.world.models import BodyKind, CelestialBody, WorldInitialState

FloatArray = NDArray[np.float64]


class InitialStateGenerator(Protocol):
    id: str
    version: str

    def generate(
        self, config: GeneratorConfig, seed: int, candidate_index: int, config_sha256: str
    ) -> WorldInitialState: ...


def _rng(seed: int, candidate_index: int, stream_id: int = 0) -> np.random.Generator:
    sequence = np.random.SeedSequence([seed, candidate_index, stream_id])
    return np.random.Generator(np.random.PCG64(sequence))


def _sample_physical(
    config: GeneratorConfig, rng: np.random.Generator
) -> tuple[FloatArray, FloatArray, list[dict[str, float]]]:
    star_solar = rng.uniform(*config.stellar_mass_solar, size=3)
    planet_earth = float(rng.uniform(*config.planet_mass_earth))
    density = float(rng.uniform(*config.planet_density_kg_m3))
    masses = np.concatenate((star_solar * M_SUN_KG, np.array([planet_earth * M_EARTH_KG])))
    star_radii = R_SUN_M * np.power(star_solar, 0.8)
    planet_radius = (3.0 * masses[3] / (4.0 * math.pi * density)) ** (1.0 / 3.0)
    radii = np.concatenate((star_radii, np.array([planet_radius])))
    luminosity_solar = np.power(star_solar, 3.5)
    properties = [
        {"luminosity_w": float(value * 3.828e26), "mass_luminosity_model": "power_3.5_v1"}
        for value in luminosity_solar
    ]
    properties.append({"mean_density_kg_m3": density, "composition_profile_id": "rocky_mean_v1"})
    return masses, radii, properties


def _remove_barycenter(
    masses: FloatArray, positions: FloatArray, velocities: FloatArray
) -> tuple[FloatArray, FloatArray]:
    total = float(np.sum(masses))
    if not math.isfinite(total) or total <= 0:
        raise ValueError("generator requires positive finite total mass")
    positions = positions - np.sum(masses[:, None] * positions, axis=0) / total
    velocities = velocities - np.sum(masses[:, None] * velocities, axis=0) / total
    return positions, velocities


def _orientation(rng: np.random.Generator) -> tuple[FloatArray, FloatArray]:
    normal = rng.normal(size=3)
    normal /= np.linalg.norm(normal)
    helper = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.8 else np.array([0.0, 1.0, 0.0])
    first = np.cross(normal, helper)
    first /= np.linalg.norm(first)
    second = np.cross(normal, first)
    phase = rng.uniform(0.0, 2.0 * math.pi)
    radial = math.cos(phase) * first + math.sin(phase) * second
    tangent = -math.sin(phase) * first + math.cos(phase) * second
    return radial, tangent


def _add_two_body_relative(
    positions: FloatArray,
    velocities: FloatArray,
    first_indices: tuple[int, ...],
    first_mass: float,
    second_index: int,
    second_mass: float,
    separation: float,
    velocity_factor: float,
    rng: np.random.Generator,
) -> None:
    radial, tangent = _orientation(rng)
    total = first_mass + second_mass
    relative_speed = math.sqrt(G * total / separation) * velocity_factor
    first_position = -second_mass / total * separation * radial
    second_position = first_mass / total * separation * radial
    first_velocity = -second_mass / total * relative_speed * tangent
    second_velocity = first_mass / total * relative_speed * tangent
    positions[list(first_indices)] += first_position
    velocities[list(first_indices)] += first_velocity
    positions[second_index] += second_position
    velocities[second_index] += second_velocity


def _state(
    generator_id: str,
    config: GeneratorConfig,
    seed: int,
    candidate_index: int,
    config_sha256: str,
    masses: FloatArray,
    radii: FloatArray,
    positions: FloatArray,
    velocities: FloatArray,
    properties: list[dict[str, float]],
) -> WorldInitialState:
    bodies = []
    for index in range(4):
        is_star = index < 3
        bodies.append(
            CelestialBody(
                id=f"star-{index + 1}" if is_star else "planet-1",
                kind=BodyKind.STAR if is_star else BodyKind.PLANET,
                mass_kg=float(masses[index]),
                radius_m=float(radii[index]),
                position_m=tuple(float(x) for x in positions[index]),
                velocity_mps=tuple(float(x) for x in velocities[index]),
                physical_profile_id="approx_main_sequence_v1" if is_star else "rocky_density_v1",
                properties=properties[index],
            )
        )
    return WorldInitialState(
        world_id=f"candidate-{candidate_index:06d}",
        seed=seed,
        candidate_index=candidate_index,
        generator_id=generator_id,
        generator_version=config.version,
        config_sha256=config_sha256,
        bodies=tuple(bodies),
    )


class CartesianGenerator:
    id = "cartesian"
    version = "0.1.0"

    def generate(
        self, config: GeneratorConfig, seed: int, candidate_index: int, config_sha256: str
    ) -> WorldInitialState:
        rng = _rng(seed, candidate_index)
        masses, radii, properties = _sample_physical(config, rng)
        positions = rng.normal(size=(4, 3)) * config.characteristic_length_m
        speed_scale = math.sqrt(G * float(np.sum(masses)) / config.characteristic_length_m)
        velocities = rng.normal(size=(4, 3)) * speed_scale * config.velocity_scale_factor
        positions, velocities = _remove_barycenter(masses, positions, velocities)
        return _state(
            self.id, config, seed, candidate_index, config_sha256,
            masses, radii, positions, velocities, properties,
        )


class HierarchicalOrbitGenerator:
    id = "hierarchical_orbit"
    version = "0.1.0"

    def generate(
        self, config: GeneratorConfig, seed: int, candidate_index: int, config_sha256: str
    ) -> WorldInitialState:
        rng = _rng(seed, candidate_index)
        masses, radii, properties = _sample_physical(config, rng)
        positions = np.zeros((4, 3), dtype=np.float64)
        velocities = np.zeros((4, 3), dtype=np.float64)
        inner = float(config.inner_orbit_radius_m)
        outer = float(config.outer_orbit_radius_m)
        planet_orbit = float(config.planet_orbit_radius_m)

        _add_two_body_relative(
            positions, velocities, (0,), masses[0], 1, masses[1], inner,
            config.velocity_scale_factor, rng,
        )
        _add_two_body_relative(
            positions, velocities, (0, 1), masses[0] + masses[1], 2, masses[2], outer,
            config.velocity_scale_factor, rng,
        )
        if config.planet_orbit_host == "barycenter":
            host_indices = (0, 1, 2)
            host_mass = float(np.sum(masses[:3]))
            host_position = np.sum(masses[:3, None] * positions[:3], axis=0) / host_mass
            host_velocity = np.sum(masses[:3, None] * velocities[:3], axis=0) / host_mass
        else:
            host_index = int(config.planet_orbit_host[-1]) - 1
            host_indices = (host_index,)
            host_mass = float(masses[host_index])
            host_position = positions[host_index].copy()
            host_velocity = velocities[host_index].copy()
        radial, tangent = _orientation(rng)
        positions[3] = host_position + planet_orbit * radial
        planet_speed = math.sqrt(G * (host_mass + masses[3]) / planet_orbit)
        velocities[3] = host_velocity + planet_speed * tangent
        # Apply the planet's two-body recoil before final global normalization.
        recoil_v = masses[3] / (host_mass + masses[3]) * (velocities[3] - host_velocity)
        velocities[list(host_indices)] -= recoil_v
        positions, velocities = _remove_barycenter(masses, positions, velocities)
        return _state(
            self.id, config, seed, candidate_index, config_sha256,
            masses, radii, positions, velocities, properties,
        )


GENERATORS: dict[str, InitialStateGenerator] = {
    "cartesian": CartesianGenerator(),
    "hierarchical_orbit": HierarchicalOrbitGenerator(),
}


def generate_initial_state(
    config: AnyGeneratorConfig, seed: int, candidate_index: int, config_sha256: str
) -> WorldInitialState:
    if config.type == "stochastic_conics":
        from threebody.world.stochastic_conics import (
            generate_bounded_stochastic_state,
            generate_stochastic_state,
        )

        generate = (
            generate_bounded_stochastic_state
            if config.version in {"0.4.1", "0.4.2"}
            else generate_stochastic_state
        )
        return generate(config, seed, candidate_index, config_sha256).state
    generator = GENERATORS[config.type]
    if config.version != generator.version:
        raise ValueError(
            f"unsupported generator version {config.version!r}; expected {generator.version!r}"
        )
    return generator.generate(config, seed, candidate_index, config_sha256)


def generate_initial_state_with_metadata(
    config: AnyGeneratorConfig, seed: int, candidate_index: int, config_sha256: str
) -> tuple[WorldInitialState, object | None, tuple[object, ...]]:
    if config.type == "stochastic_conics":
        from threebody.world.stochastic_conics import (
            generate_bounded_stochastic_state,
            generate_stochastic_state,
        )

        generate = (
            generate_bounded_stochastic_state
            if config.version in {"0.4.1", "0.4.2"}
            else generate_stochastic_state
        )
        generated = generate(config, seed, candidate_index, config_sha256)
        return generated.state, generated.metadata, generated.initial_contacts
    return generate_initial_state(config, seed, candidate_index, config_sha256), None, ()

