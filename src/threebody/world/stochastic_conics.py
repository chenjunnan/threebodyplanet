from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict

from threebody.common.constants import M_EARTH_KG, M_SUN_KG, R_SUN_M, G
from threebody.config.models import ScalarDistributionConfig, StochasticConicsGeneratorConfig
from threebody.world.models import BodyKind, CelestialBody, WorldInitialState

FloatArray = NDArray[np.float64]
HOSTS = ("star-1", "star-2", "star-3", "barycenter")


class GenerationError(ValueError):
    def __init__(self, reason_code: str, message: str):
        self.reason_code = reason_code
        super().__init__(message)


class _MetadataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ConicGenerationRecord(_MetadataModel):
    relation: Literal["inner", "outer", "planet"]
    primary_body_ids: tuple[str, ...]
    secondary_body_id: str
    host: str | None
    periapsis_m: float
    eccentricity: float
    conic_type: Literal["ELLIPTIC", "HYPERBOLIC"]
    true_anomaly_rad: float
    inclination_rad: float
    longitude_ascending_node_rad: float
    argument_periapsis_rad: float
    semilatus_rectum_m: float
    radius_m: float
    gravitational_parameter_m3_s2: float
    pre_kick_specific_energy_j_kg: float
    pre_kick_specific_angular_momentum_m2_s: float
    relative_position_m: tuple[float, float, float]
    relative_velocity_pre_kick_mps: tuple[float, float, float]
    velocity_kick_mps: tuple[float, float, float]
    relative_velocity_post_kick_mps: tuple[float, float, float]


class PhysicalGenerationRecord(_MetadataModel):
    stellar_mass_solar: tuple[float, float, float]
    planet_mass_earth: float
    planet_density_kg_m3: float
    host_probabilities: dict[str, float]


class GenerationMetadata(_MetadataModel):
    schema_version: Literal["0.4.0"] = "0.4.0"
    generator_id: Literal["stochastic_conics"] = "stochastic_conics"
    generator_version: Literal["0.4.0"] = "0.4.0"
    world_id: str
    seed: int
    candidate_index: int
    rng_algorithm: Literal["PCG64"] = "PCG64"
    substream_scheme: Literal["SeedSequence([seed,candidate_index,stream_id])"] = (
        "SeedSequence([seed,candidate_index,stream_id])"
    )
    distribution_schema_version: Literal["0.4.0"] = "0.4.0"
    sampled_physical: PhysicalGenerationRecord
    selected_planet_host: str
    conics: tuple[ConicGenerationRecord, ConicGenerationRecord, ConicGenerationRecord]
    post_kick_elements_claimed: Literal[False] = False
    scientific_claim: str = (
        "stochastic initial state only; no stability, survival, or habitability claim"
    )


class InitialContactOutcome(_MetadataModel):
    schema_version: Literal["0.4.0"] = "0.4.0"
    world_id: str
    status: Literal["INITIAL_CONTACT"] = "INITIAL_CONTACT"
    event_type: Literal["INITIAL_COLLISION"] = "INITIAL_COLLISION"
    t_s: Literal[0.0] = 0.0
    terminal: Literal[True] = True
    integration_performed: Literal[False] = False
    body_ids: tuple[str, str]
    center_distance_m: float
    contact_distance_m: float
    surface_gap_m: float
    detection_method: Literal["initial_sphere_geometry"] = "initial_sphere_geometry"
    message: str = "bodies overlap or touch at t=0; IAS15 integration is not attempted"


@dataclass(frozen=True)
class GeneratedStochasticState:
    state: WorldInitialState
    metadata: GenerationMetadata
    initial_contacts: tuple[InitialContactOutcome, ...]


@dataclass(frozen=True)
class ConicCartesian:
    position_m: FloatArray
    velocity_mps: FloatArray
    record: ConicGenerationRecord


def _rng(seed: int, candidate_index: int, stream_id: int) -> np.random.Generator:
    sequence = np.random.SeedSequence([seed, candidate_index, stream_id])
    return np.random.Generator(np.random.PCG64(sequence))


def _sample(config: ScalarDistributionConfig, rng: np.random.Generator) -> float:
    if config.min == config.max:
        return config.min
    if config.distribution == "uniform":
        return float(rng.uniform(config.min, config.max))
    return float(math.exp(rng.uniform(math.log(config.min), math.log(config.max))))


def _unit_vector(rng: np.random.Generator) -> FloatArray:
    for _ in range(8):
        vector = np.asarray(rng.normal(size=3), dtype=np.float64)
        norm = float(np.linalg.norm(vector))
        if math.isfinite(norm) and norm > 0.0:
            return vector / norm
    raise GenerationError("ISOTROPIC_DIRECTION_FAILED", "could not sample a finite direction")


def build_conic_cartesian(
    *,
    relation: Literal["inner", "outer", "planet"],
    primary_body_ids: tuple[str, ...],
    secondary_body_id: str,
    host: str | None,
    primary_mass_kg: float,
    secondary_mass_kg: float,
    periapsis_m: float,
    eccentricity: float,
    orientation_rng: np.random.Generator,
    kick_rng: np.random.Generator,
    kick_config: Any,
) -> ConicCartesian:
    if eccentricity == 1.0:
        raise GenerationError(
            "PARABOLIC_UNSUPPORTED", "parabolic conics (e=1) are explicitly unsupported"
        )
    if not (math.isfinite(periapsis_m) and periapsis_m > 0.0):
        raise GenerationError("INVALID_PERIAPSIS", "periapsis must be finite and positive")
    if not (math.isfinite(eccentricity) and eccentricity >= 0.0):
        raise GenerationError(
            "INVALID_ECCENTRICITY", "eccentricity must be finite and non-negative"
        )
    if not all(
        math.isfinite(mass) and mass > 0.0
        for mass in (primary_mass_kg, secondary_mass_kg)
    ):
        raise GenerationError("INVALID_MASS", "conic masses must be finite and positive")

    cos_i = float(orientation_rng.uniform(-1.0, 1.0))
    inclination = math.acos(cos_i)
    node = float(orientation_rng.uniform(0.0, 2.0 * math.pi))
    periapsis_argument = float(orientation_rng.uniform(0.0, 2.0 * math.pi))
    if eccentricity < 1.0:
        anomaly = float(orientation_rng.uniform(-math.pi, math.pi))
        conic_type = "ELLIPTIC"
    else:
        limit = math.acos(-1.0 / eccentricity)
        safe_limit = limit * (1.0 - 1.0e-12)
        anomaly = float(orientation_rng.uniform(-safe_limit, safe_limit))
        conic_type = "HYPERBOLIC"

    cos_node, sin_node = math.cos(node), math.sin(node)
    cos_arg, sin_arg = math.cos(periapsis_argument), math.sin(periapsis_argument)
    p_hat = np.asarray(
        [
            cos_node * cos_arg - sin_node * sin_arg * cos_i,
            sin_node * cos_arg + cos_node * sin_arg * cos_i,
            sin_arg * math.sin(inclination),
        ],
        dtype=np.float64,
    )
    q_hat = np.asarray(
        [
            -cos_node * sin_arg - sin_node * cos_arg * cos_i,
            -sin_node * sin_arg + cos_node * cos_arg * cos_i,
            cos_arg * math.sin(inclination),
        ],
        dtype=np.float64,
    )
    mu = G * (primary_mass_kg + secondary_mass_kg)
    semilatus = periapsis_m * (1.0 + eccentricity)
    if not math.isfinite(mu) or not math.isfinite(semilatus) or semilatus <= 0.0:
        raise GenerationError("UNREPRESENTABLE_CONIC", "conic parameters exceed float64 range")
    denominator = 1.0 + eccentricity * math.cos(anomaly)
    if denominator <= 0.0 or not math.isfinite(denominator):
        raise GenerationError("INVALID_TRUE_ANOMALY", "true anomaly is outside conic domain")
    radius = semilatus / denominator
    angular_momentum = math.sqrt(mu * semilatus)
    position = radius * (math.cos(anomaly) * p_hat + math.sin(anomaly) * q_hat)
    velocity = (mu / angular_momentum) * (
        -math.sin(anomaly) * p_hat + (eccentricity + math.cos(anomaly)) * q_hat
    )
    if not all(
        math.isfinite(value)
        for value in (mu, semilatus, radius, angular_momentum, *position, *velocity)
    ):
        raise GenerationError("UNREPRESENTABLE_CONIC", "conic state exceeds float64 range")

    pre_kick_velocity = velocity.copy()
    if kick_config.distribution == "none":
        kick = np.zeros(3, dtype=np.float64)
    else:
        fraction = _sample(kick_config.speed_scale_fraction, kick_rng)
        kick = _unit_vector(kick_rng) * fraction * float(np.linalg.norm(pre_kick_velocity))
    velocity = pre_kick_velocity + kick
    if not np.all(np.isfinite(kick)) or not np.all(np.isfinite(velocity)):
        raise GenerationError("UNREPRESENTABLE_KICK", "velocity kick exceeds float64 range")
    energy = 0.5 * float(np.dot(pre_kick_velocity, pre_kick_velocity)) - mu / radius
    record = ConicGenerationRecord(
        relation=relation,
        primary_body_ids=primary_body_ids,
        secondary_body_id=secondary_body_id,
        host=host,
        periapsis_m=periapsis_m,
        eccentricity=eccentricity,
        conic_type=conic_type,
        true_anomaly_rad=anomaly,
        inclination_rad=inclination,
        longitude_ascending_node_rad=node,
        argument_periapsis_rad=periapsis_argument,
        semilatus_rectum_m=semilatus,
        radius_m=radius,
        gravitational_parameter_m3_s2=mu,
        pre_kick_specific_energy_j_kg=energy,
        pre_kick_specific_angular_momentum_m2_s=angular_momentum,
        relative_position_m=tuple(float(x) for x in position),
        relative_velocity_pre_kick_mps=tuple(float(x) for x in pre_kick_velocity),
        velocity_kick_mps=tuple(float(x) for x in kick),
        relative_velocity_post_kick_mps=tuple(float(x) for x in velocity),
    )
    return ConicCartesian(position, velocity, record)


def _embed_relative(
    positions: FloatArray,
    velocities: FloatArray,
    masses: FloatArray,
    primary_indices: tuple[int, ...],
    secondary_index: int,
    relative: ConicCartesian,
) -> None:
    primary_mass = float(np.sum(masses[list(primary_indices)]))
    secondary_mass = float(masses[secondary_index])
    total_mass = primary_mass + secondary_mass
    primary_position = (
        np.sum(masses[list(primary_indices), None] * positions[list(primary_indices)], axis=0)
        / primary_mass
    )
    primary_velocity = (
        np.sum(masses[list(primary_indices), None] * velocities[list(primary_indices)], axis=0)
        / primary_mass
    )
    positions[list(primary_indices)] -= secondary_mass / total_mass * relative.position_m
    velocities[list(primary_indices)] -= secondary_mass / total_mass * relative.velocity_mps
    positions[secondary_index] = (
        primary_position + primary_mass / total_mass * relative.position_m
    )
    velocities[secondary_index] = (
        primary_velocity + primary_mass / total_mass * relative.velocity_mps
    )


def _embed_planet_relative(
    positions: FloatArray,
    velocities: FloatArray,
    masses: FloatArray,
    host_indices: tuple[int, ...],
    relative: ConicCartesian,
) -> None:
    host_mass = float(np.sum(masses[list(host_indices)]))
    host_position = (
        np.sum(masses[list(host_indices), None] * positions[list(host_indices)], axis=0)
        / host_mass
    )
    host_velocity = (
        np.sum(masses[list(host_indices), None] * velocities[list(host_indices)], axis=0)
        / host_mass
    )
    stellar_mass = float(np.sum(masses[:3]))
    planet_mass = float(masses[3])
    combined_mass = stellar_mass + planet_mass
    position_shift = -planet_mass / combined_mass * (host_position + relative.position_m)
    velocity_shift = -planet_mass / combined_mass * (host_velocity + relative.velocity_mps)
    positions[:3] += position_shift
    velocities[:3] += velocity_shift
    positions[3] = host_position + position_shift + relative.position_m
    velocities[3] = host_velocity + velocity_shift + relative.velocity_mps


def _contacts(state: WorldInitialState) -> tuple[InitialContactOutcome, ...]:
    outcomes: list[InitialContactOutcome] = []
    for first_index, first in enumerate(state.bodies):
        for second in state.bodies[first_index + 1 :]:
            distance = float(
                np.linalg.norm(np.asarray(first.position_m) - np.asarray(second.position_m))
            )
            contact_distance = first.radius_m + second.radius_m
            if distance <= contact_distance:
                outcomes.append(
                    InitialContactOutcome(
                        world_id=state.world_id,
                        body_ids=(first.id, second.id),
                        center_distance_m=distance,
                        contact_distance_m=contact_distance,
                        surface_gap_m=distance - contact_distance,
                    )
                )
    return tuple(outcomes)


def generate_stochastic_state(
    config: StochasticConicsGeneratorConfig,
    seed: int,
    candidate_index: int,
    config_sha256: str,
) -> GeneratedStochasticState:
    star_solar = np.asarray(
        [_sample(config.stars.mass_solar, _rng(seed, candidate_index, 100 + i)) for i in range(3)]
    )
    planet_earth = _sample(config.planet.mass_earth, _rng(seed, candidate_index, 110))
    density = _sample(config.planet.density_kg_m3, _rng(seed, candidate_index, 111))
    with np.errstate(over="ignore", invalid="ignore"):
        masses = np.concatenate((star_solar * M_SUN_KG, [planet_earth * M_EARTH_KG]))
        star_radii = R_SUN_M * np.power(star_solar, 0.8)
        planet_radius = (3.0 * masses[3] / (4.0 * math.pi * density)) ** (1.0 / 3.0)
    radii = np.concatenate((star_radii, [planet_radius]))
    if not np.all(np.isfinite(masses)) or not np.all(np.isfinite(radii)):
        raise GenerationError(
            "UNREPRESENTABLE_PHYSICAL_SAMPLE", "sampled mass or radius exceeds float64 range"
        )

    host_weights = config.geometry.planet.host_weights
    weights = np.asarray([host_weights[name] for name in HOSTS], dtype=np.float64)
    weights /= float(np.sum(weights))
    host = str(_rng(seed, candidate_index, 120).choice(HOSTS, p=weights))

    conic_configs = (
        ("inner", config.geometry.inner, ("star-1",), "star-2", None, (0,), 1, 200),
        ("outer", config.geometry.outer, ("star-1", "star-2"), "star-3", None, (0, 1), 2, 210),
    )
    positions = np.zeros((4, 3), dtype=np.float64)
    velocities = np.zeros((4, 3), dtype=np.float64)
    records: list[ConicGenerationRecord] = []
    for (
        relation,
        conic_config,
        primary_ids,
        secondary_id,
        relation_host,
        indices,
        second,
        stream,
    ) in conic_configs:
        q = _sample(conic_config.periapsis_m, _rng(seed, candidate_index, stream))
        e = _sample(conic_config.eccentricity, _rng(seed, candidate_index, stream + 1))
        relative = build_conic_cartesian(
            relation=relation,
            primary_body_ids=primary_ids,
            secondary_body_id=secondary_id,
            host=relation_host,
            primary_mass_kg=float(np.sum(masses[list(indices)])),
            secondary_mass_kg=float(masses[second]),
            periapsis_m=q,
            eccentricity=e,
            orientation_rng=_rng(seed, candidate_index, stream + 2),
            kick_rng=_rng(seed, candidate_index, stream + 3),
            kick_config=config.geometry.velocity_kick,
        )
        _embed_relative(positions, velocities, masses, indices, second, relative)
        records.append(relative.record)

    if host == "barycenter":
        planet_primary_indices = (0, 1, 2)
        planet_primary_ids = ("star-1", "star-2", "star-3")
    else:
        host_index = HOSTS.index(host)
        planet_primary_indices = (host_index,)
        planet_primary_ids = (host,)
    q = _sample(config.geometry.planet.periapsis_m, _rng(seed, candidate_index, 220))
    e = _sample(config.geometry.planet.eccentricity, _rng(seed, candidate_index, 221))
    planet_relative = build_conic_cartesian(
        relation="planet",
        primary_body_ids=planet_primary_ids,
        secondary_body_id="planet-1",
        host=host,
        primary_mass_kg=float(np.sum(masses[list(planet_primary_indices)])),
        secondary_mass_kg=float(masses[3]),
        periapsis_m=q,
        eccentricity=e,
        orientation_rng=_rng(seed, candidate_index, 222),
        kick_rng=_rng(seed, candidate_index, 223),
        kick_config=config.geometry.velocity_kick,
    )
    # Apply the planet recoil as a uniform stellar-system translation. This preserves both
    # previously constructed stellar relative conics and the selected host/planet relative state.
    _embed_planet_relative(
        positions, velocities, masses, planet_primary_indices, planet_relative
    )
    records.append(planet_relative.record)

    total_mass = float(np.sum(masses))
    positions -= np.sum(masses[:, None] * positions, axis=0) / total_mass
    velocities -= np.sum(masses[:, None] * velocities, axis=0) / total_mass
    if not np.all(np.isfinite(positions)) or not np.all(np.isfinite(velocities)):
        raise GenerationError("NONFINITE_FINAL_STATE", "final barycentric state is non-finite")

    with np.errstate(over="ignore", invalid="ignore"):
        luminosities = np.power(star_solar, 3.5) * 3.828e26
    if not np.all(np.isfinite(luminosities)):
        raise GenerationError(
            "UNREPRESENTABLE_PHYSICAL_SAMPLE", "sampled luminosity exceeds float64 range"
        )
    properties: list[dict[str, Any]] = [
        {
            "luminosity_w": float(luminosity),
            "mass_luminosity_model": "power_3.5_v1",
        }
        for luminosity in luminosities
    ]
    properties.append(
        {"mean_density_kg_m3": density, "composition_profile_id": "rocky_mean_v1"}
    )
    bodies = tuple(
        CelestialBody(
            id=f"star-{index + 1}" if index < 3 else "planet-1",
            kind=BodyKind.STAR if index < 3 else BodyKind.PLANET,
            mass_kg=float(masses[index]),
            radius_m=float(radii[index]),
            position_m=tuple(float(x) for x in positions[index]),
            velocity_mps=tuple(float(x) for x in velocities[index]),
            physical_profile_id="approx_main_sequence_v1" if index < 3 else "rocky_density_v1",
            properties=properties[index],
        )
        for index in range(4)
    )
    state = WorldInitialState(
        world_id=f"candidate-{candidate_index:06d}",
        seed=seed,
        candidate_index=candidate_index,
        generator_id=config.type,
        generator_version=config.version,
        config_sha256=config_sha256,
        bodies=bodies,
    )
    metadata = GenerationMetadata(
        world_id=state.world_id,
        seed=seed,
        candidate_index=candidate_index,
        sampled_physical=PhysicalGenerationRecord(
            stellar_mass_solar=tuple(float(value) for value in star_solar),
            planet_mass_earth=planet_earth,
            planet_density_kg_m3=density,
            host_probabilities={name: float(weights[i]) for i, name in enumerate(HOSTS)},
        ),
        selected_planet_host=host,
        conics=tuple(records),
    )
    return GeneratedStochasticState(state, metadata, _contacts(state))
