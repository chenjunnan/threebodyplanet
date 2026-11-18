from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict

from threebody.common.constants import M_EARTH_KG, M_SUN_KG, R_SUN_M, G
from threebody.config.models import (
    AtmospherePriorConfig,
    BoundedStochasticConicsGeneratorConfig,
    PlanetRotationDistributionConfig,
    ScalarDistributionConfig,
    StochasticConicsGeneratorConfig,
)
from threebody.physics.stellar.main_sequence import main_sequence_properties
from threebody.world.models import BodyKind, CelestialBody, WorldInitialState

FloatArray = NDArray[np.float64]
HOSTS = ("star-1", "star-2", "star-3", "barycenter")
BOLTZMANN_J_K = 1.380649e-23
ATOMIC_MASS_KG = 1.66053906660e-27
SIGMA_W_M2_K4 = 5.670374419e-8
GAS_MOLAR_MASS_KG_MOL = {
    "nitrogen": 0.0280134,
    "carbon_dioxide": 0.0440095,
    "argon": 0.039948,
    "water_vapor": 0.01801528,
    "oxygen": 0.0319988,
}


class GenerationError(ValueError):
    def __init__(self, reason_code: str, message: str):
        self.reason_code = reason_code
        super().__init__(message)


class ConstraintExhausted(GenerationError):
    def __init__(self, metadata: dict[str, Any]):
        self.metadata = metadata
        super().__init__(
            "GENERATION_CONSTRAINT_EXHAUSTED",
            "bounded initial-state constraints were not satisfied within max_attempts",
        )


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


class ConstraintMetrics(_MetadataModel):
    planet_star_distances_m: dict[str, float]
    nearest_planet_star_distance_m: float
    maximum_planet_star_distance_m: float
    maximum_stellar_pair_distance_m: float
    planet_speed_relative_stellar_com_mps: float
    selected_host_escape_speed_proxy_mps: float
    selected_host_speed_fraction: float
    combined_potential_escape_speed_proxy_mps: float
    combined_potential_speed_fraction: float
    initial_contact_count: int


class ConstraintAttemptRecord(_MetadataModel):
    attempt: int
    accepted: bool
    reason_codes: tuple[str, ...]
    metrics: ConstraintMetrics


class GenerationMetadataV041(_MetadataModel):
    schema_version: Literal["0.4.1", "0.4.2", "0.4.3"] = "0.4.1"
    generator_id: Literal["stochastic_conics"] = "stochastic_conics"
    generator_version: Literal["0.4.1", "0.4.2", "0.4.3"] = "0.4.1"
    world_id: str
    seed: int
    candidate_index: int
    rng_algorithm: Literal["PCG64"] = "PCG64"
    substream_scheme: Literal[
        "SeedSequence([seed,candidate_index,attempt,stream_id])"
    ] = "SeedSequence([seed,candidate_index,attempt,stream_id])"
    distribution_schema_version: Literal["0.4.1", "0.4.2", "0.4.3"] = "0.4.1"
    constraint_mode: Literal["unrestricted", "bounded"]
    constraints: dict[str, Any]
    selected_attempt: int
    attempt_count: int
    attempts: tuple[ConstraintAttemptRecord, ...]
    sampled_physical: PhysicalGenerationRecord
    selected_planet_host: str
    conics: tuple[ConicGenerationRecord, ConicGenerationRecord, ConicGenerationRecord]
    post_kick_elements_claimed: Literal[False] = False
    scientific_claim: str = (
        "bounded stochastic initial-state preference only; no stability, survival, permanent "
        "binding, or habitability claim"
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


def _rng(
    seed: int, candidate_index: int, stream_id: int, attempt: int | None = None
) -> np.random.Generator:
    entropy = (
        [seed, candidate_index, stream_id]
        if attempt is None
        else [seed, candidate_index, attempt, stream_id]
    )
    sequence = np.random.SeedSequence(entropy)
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


def _spin_properties(
    rng: np.random.Generator, orbital_normal: FloatArray, period_s: float
) -> dict[str, Any]:
    """Kinematic spin metadata; no oblateness, gravity multipole or stellar beaming."""
    axis = _unit_vector(rng)
    prime = np.cross(axis, np.asarray([0.0, 0.0, 1.0]))
    if float(np.linalg.norm(prime)) < 1.0e-8:
        prime = np.cross(axis, np.asarray([1.0, 0.0, 0.0]))
    prime /= np.linalg.norm(prime)
    tilt = math.acos(float(np.clip(np.dot(axis, orbital_normal), -1.0, 1.0)))
    return {
        "rotation_axis_inertial_unit": [float(v) for v in axis],
        "prime_meridian_inertial_unit": [float(v) for v in prime],
        "obliquity_to_planet_reference_orbit_rad": tilt,
        "rotation_epoch_s": 0.0,
        "rotation_rate_rad_s": math.tau / period_s,
        "rotation_period_s": period_s,
        "rotation_model": "kinematic_fixed_axis_v1",
    }


def _atmosphere_hypothesis(
    *,
    config: AtmospherePriorConfig,
    occurrence_rng: np.random.Generator,
    composition_rng: np.random.Generator,
    mass_rng: np.random.Generator,
    albedo_rng: np.random.Generator,
    planet_mass_kg: float,
    planet_radius_m: float,
    rotation_rate_rad_s: float,
    total_stellar_flux_w_m2: float,
) -> dict[str, Any]:
    """Versioned rocky-atmosphere prior; diagnostic plausibility, never proof."""
    albedo = _sample(config.albedo, albedo_rng)
    equilibrium_k = (
        max(0.0, (1.0 - albedo) * total_stellar_flux_w_m2)
        / (4.0 * SIGMA_W_M2_K4)
    ) ** 0.25
    upper_temperature_k = min(2500.0, max(120.0, 2.5 * equilibrium_k))
    effective_escape_sq = max(
        0.0,
        2.0 * G * planet_mass_kg / planet_radius_m
        - (rotation_rate_rad_s * planet_radius_m) ** 2,
    )
    gases = tuple(GAS_MOLAR_MASS_KG_MOL)
    alpha = np.asarray(
        [config.composition_dirichlet_alpha[name] for name in gases], dtype=np.float64
    )
    fractions = composition_rng.dirichlet(alpha)
    mean_molar_mass = float(
        sum(fractions[i] * GAS_MOLAR_MASS_KG_MOL[name] for i, name in enumerate(gases))
    )
    molecule_mass = mean_molar_mass / 6.02214076e23
    jeans = (
        molecule_mass * effective_escape_sq / (2.0 * BOLTZMANN_J_K * upper_temperature_k)
        if upper_temperature_k > 0.0
        else 0.0
    )
    low, high = config.jeans_transition
    retention_weight = min(1.0, max(0.0, (jeans - low) / (high - low)))
    draw = float(occurrence_rng.random())
    probability = config.occurrence_probability * retention_weight
    exists = draw < probability
    mass_fraction = _sample(config.mass_fraction, mass_rng) if exists else 0.0
    atmosphere_mass = planet_mass_kg * mass_fraction
    pressure = (
        G * planet_mass_kg / planet_radius_m**2
        * atmosphere_mass / (4.0 * math.pi * planet_radius_m**2)
    )
    composition = {name: float(fractions[i]) if exists else 0.0 for i, name in enumerate(gases)}
    # Limit the vapor mole fraction to a coarse saturation proxy and return excess to N2.
    # This is a consistency bound, not a hydrological-cycle calculation.
    if exists and pressure > 0.0 and equilibrium_k < 647.0:
        saturation = 611.657 * math.exp(
            2.5e6 / 461.5 * (1.0 / 273.16 - 1.0 / max(120.0, equilibrium_k))
        )
        allowed = min(1.0, saturation / pressure)
        excess = max(0.0, composition["water_vapor"] - allowed)
        composition["water_vapor"] -= excess
        composition["nitrogen"] += excess
        mean_molar_mass = sum(
            composition[name] * GAS_MOLAR_MASS_KG_MOL[name] for name in gases
        )
    return {
        "model": config.model,
        "status": "PRESENT_PRIOR_SAMPLE" if exists else "ABSENT_PRIOR_SAMPLE",
        "is_observation": False,
        "atmosphere_mass_kg": atmosphere_mass,
        "mass_fraction": mass_fraction,
        "composition_mole_fraction": composition,
        "mean_molar_mass_kg_mol": mean_molar_mass if exists else None,
        "sampled_albedo": albedo,
        "total_initial_stellar_flux_w_m2": total_stellar_flux_w_m2,
        "equilibrium_temperature_proxy_k": equilibrium_k,
        "upper_atmosphere_temperature_proxy_k": upper_temperature_k,
        "rotation_adjusted_escape_speed_proxy_mps": math.sqrt(effective_escape_sq),
        "jeans_parameter_proxy": jeans,
        "retention_weight": retention_weight,
        "base_occurrence_probability": config.occurrence_probability,
        "weighted_occurrence_probability": probability,
        "occurrence_draw": draw,
        "scientific_claim": (
            "seeded retention-weighted atmosphere prior; not proof of atmosphere or composition"
        ),
    }


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
    attempt: int | None = None,
    physics_version: Literal["0.4.0", "0.4.2", "0.4.3"] = "0.4.0",
    planet_rotation: PlanetRotationDistributionConfig | None = None,
    atmosphere_prior: AtmospherePriorConfig | None = None,
) -> GeneratedStochasticState:
    rng = lambda stream_id: _rng(seed, candidate_index, stream_id, attempt)  # noqa: E731
    star_solar = np.asarray(
        [_sample(config.stars.mass_solar, rng(100 + i)) for i in range(3)]
    )
    planet_earth = _sample(config.planet.mass_earth, rng(110))
    density = _sample(config.planet.density_kg_m3, rng(111))
    with np.errstate(over="ignore", invalid="ignore"):
        masses = np.concatenate((star_solar * M_SUN_KG, [planet_earth * M_EARTH_KG]))
        star_radii = (
            np.asarray([main_sequence_properties(float(m))[0] for m in star_solar])
            if physics_version in {"0.4.2", "0.4.3"}
            else R_SUN_M * np.power(star_solar, 0.8)
        )
        planet_radius = (3.0 * masses[3] / (4.0 * math.pi * density)) ** (1.0 / 3.0)
    radii = np.concatenate((star_radii, [planet_radius]))
    if not np.all(np.isfinite(masses)) or not np.all(np.isfinite(radii)):
        raise GenerationError(
            "UNREPRESENTABLE_PHYSICAL_SAMPLE", "sampled mass or radius exceeds float64 range"
        )

    host_weights = config.geometry.planet.host_weights
    weights = np.asarray([host_weights[name] for name in HOSTS], dtype=np.float64)
    weights /= float(np.sum(weights))
    host = str(rng(120).choice(HOSTS, p=weights))

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
        q = _sample(conic_config.periapsis_m, rng(stream))
        e = _sample(conic_config.eccentricity, rng(stream + 1))
        relative = build_conic_cartesian(
            relation=relation,
            primary_body_ids=primary_ids,
            secondary_body_id=secondary_id,
            host=relation_host,
            primary_mass_kg=float(np.sum(masses[list(indices)])),
            secondary_mass_kg=float(masses[second]),
            periapsis_m=q,
            eccentricity=e,
            orientation_rng=rng(stream + 2),
            kick_rng=rng(stream + 3),
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
    q = _sample(config.geometry.planet.periapsis_m, rng(220))
    e = _sample(config.geometry.planet.eccentricity, rng(221))
    planet_relative = build_conic_cartesian(
        relation="planet",
        primary_body_ids=planet_primary_ids,
        secondary_body_id="planet-1",
        host=host,
        primary_mass_kg=float(np.sum(masses[list(planet_primary_indices)])),
        secondary_mass_kg=float(masses[3]),
        periapsis_m=q,
        eccentricity=e,
        orientation_rng=rng(222),
        kick_rng=rng(223),
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

    if physics_version in {"0.4.2", "0.4.3"}:
        luminosities = np.asarray(
            [main_sequence_properties(float(m))[1] for m in star_solar], dtype=np.float64
        )
    else:
        with np.errstate(over="ignore", invalid="ignore"):
            luminosities = np.power(star_solar, 3.5) * 3.828e26
    if not np.all(np.isfinite(luminosities)):
        raise GenerationError(
            "UNREPRESENTABLE_PHYSICAL_SAMPLE", "sampled luminosity exceeds float64 range"
        )
    properties: list[dict[str, Any]] = [
        {
            "luminosity_w": float(luminosity),
            "mass_luminosity_model": (
                "piecewise_main_sequence_0p3_8_v1"
                if physics_version in {"0.4.2", "0.4.3"} else "power_3.5_v1"
            ),
        }
        for luminosity in luminosities
    ]
    if physics_version in {"0.4.2", "0.4.3"}:
        # New random streams do not perturb the historical orbit draws.
        orbital_normal = np.cross(
            planet_relative.position_m, planet_relative.velocity_mps
        )
        orbital_normal /= np.linalg.norm(orbital_normal)
        for index, star_properties in enumerate(properties):
            period = float(rng(700 + index).uniform(1.0, 30.0)) * 86400.0
            star_properties.update(
                _spin_properties(rng(710 + index), orbital_normal, period)
            )
        planet_period_s = (
            86400.0
            if physics_version == "0.4.2"
            else _sample(planet_rotation.period_s, rng(721))
        )
        planet_properties: dict[str, Any] = {
            "mean_density_kg_m3": density,
            "composition_profile_id": "rocky_mean_v1",
        }
        planet_properties.update(_spin_properties(rng(720), orbital_normal, planet_period_s))
        if physics_version == "0.4.3":
            if planet_rotation is None or atmosphere_prior is None:
                raise GenerationError(
                    "MISSING_043_PRIOR", "generator 0.4.3 requires rotation/atmosphere priors"
                )
            planet_position = positions[3]
            total_flux = 0.0
            for index, luminosity in enumerate(luminosities):
                distance = float(np.linalg.norm(positions[index] - planet_position))
                total_flux += float(luminosity) / (4.0 * math.pi * distance**2)
            planet_properties["atmosphere_hypothesis"] = _atmosphere_hypothesis(
                config=atmosphere_prior,
                occurrence_rng=rng(730),
                composition_rng=rng(731),
                mass_rng=rng(732),
                albedo_rng=rng(733),
                planet_mass_kg=float(masses[3]),
                planet_radius_m=float(radii[3]),
                rotation_rate_rad_s=math.tau / planet_period_s,
                total_stellar_flux_w_m2=total_flux,
            )
        properties.append(planet_properties)
    else:
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


def _constraint_metrics(
    state: WorldInitialState, host: str, contact_count: int
) -> ConstraintMetrics:
    masses = np.asarray([body.mass_kg for body in state.bodies], dtype=np.float64)
    positions = np.asarray([body.position_m for body in state.bodies], dtype=np.float64)
    velocities = np.asarray([body.velocity_mps for body in state.bodies], dtype=np.float64)
    distances = np.linalg.norm(positions[:3] - positions[3], axis=1)
    stellar_mass = float(np.sum(masses[:3]))
    stellar_com_position = np.sum(masses[:3, None] * positions[:3], axis=0) / stellar_mass
    stellar_com_velocity = np.sum(masses[:3, None] * velocities[:3], axis=0) / stellar_mass
    planet_com_speed = float(np.linalg.norm(velocities[3] - stellar_com_velocity))
    stellar_pairs = [
        float(np.linalg.norm(positions[first] - positions[second]))
        for first in range(3)
        for second in range(first + 1, 3)
    ]
    if host == "barycenter":
        host_distance = float(np.linalg.norm(positions[3] - stellar_com_position))
        host_speed = planet_com_speed
        host_mass = stellar_mass
    else:
        host_index = int(host[-1]) - 1
        host_distance = float(distances[host_index])
        host_speed = float(np.linalg.norm(velocities[3] - velocities[host_index]))
        host_mass = float(masses[host_index])
    selected_escape = math.sqrt(2.0 * G * (host_mass + masses[3]) / host_distance)
    combined_escape = math.sqrt(
        2.0 * G * float(np.sum(masses[:3] / distances))
    )
    return ConstraintMetrics(
        planet_star_distances_m={
            f"star-{index + 1}": float(value)
            for index, value in enumerate(distances)
        },
        nearest_planet_star_distance_m=float(np.min(distances)),
        maximum_planet_star_distance_m=float(np.max(distances)),
        maximum_stellar_pair_distance_m=max(stellar_pairs),
        planet_speed_relative_stellar_com_mps=planet_com_speed,
        selected_host_escape_speed_proxy_mps=selected_escape,
        selected_host_speed_fraction=host_speed / selected_escape,
        combined_potential_escape_speed_proxy_mps=combined_escape,
        combined_potential_speed_fraction=planet_com_speed / combined_escape,
        initial_contact_count=contact_count,
    )


def _constraint_reasons(
    config: BoundedStochasticConicsGeneratorConfig,
    metrics: ConstraintMetrics,
) -> tuple[str, ...]:
    constraints = config.initial_constraints
    reasons: list[str] = []
    if metrics.initial_contact_count and not constraints.allow_initial_contact:
        reasons.append("INITIAL_CONTACT_NOT_ALLOWED")
    distances = constraints.planet_star_distance_m
    if distances.min is not None and any(
        value < distances.min for value in metrics.planet_star_distances_m.values()
    ):
        reasons.append("PLANET_STAR_DISTANCE_BELOW_MIN")
    if (
        distances.max_nearest is not None
        and metrics.nearest_planet_star_distance_m > distances.max_nearest
    ):
        reasons.append("NEAREST_STAR_DISTANCE_ABOVE_MAX")
    if (
        distances.max_each is not None
        and metrics.maximum_planet_star_distance_m > distances.max_each
    ):
        reasons.append("PLANET_STAR_DISTANCE_ABOVE_MAX_EACH")
    if (
        constraints.stellar_pair_max_distance_m is not None
        and metrics.maximum_stellar_pair_distance_m
        > constraints.stellar_pair_max_distance_m
    ):
        reasons.append("STELLAR_PAIR_DISTANCE_ABOVE_MAX")
    speed = constraints.planet_speed
    if (
        speed.absolute_max_mps is not None
        and metrics.planet_speed_relative_stellar_com_mps > speed.absolute_max_mps
    ):
        reasons.append("PLANET_SPEED_ABOVE_ABSOLUTE_MAX")
    if speed.escape_mode == "selected_host" and (
        metrics.selected_host_speed_fraction > speed.escape_speed_fraction_max
    ):
        reasons.append("SELECTED_HOST_ESCAPE_FRACTION_ABOVE_MAX")
    if speed.escape_mode == "combined_potential" and (
        metrics.combined_potential_speed_fraction > speed.escape_speed_fraction_max
    ):
        reasons.append("COMBINED_POTENTIAL_ESCAPE_FRACTION_ABOVE_MAX")
    return tuple(reasons)


def generate_bounded_stochastic_state(
    config: BoundedStochasticConicsGeneratorConfig,
    seed: int,
    candidate_index: int,
    config_sha256: str,
) -> GeneratedStochasticState:
    core_config = StochasticConicsGeneratorConfig(
        type="stochastic_conics",
        version="0.4.0",
        stars=config.stars,
        planet=config.planet,
        geometry=config.geometry,
    )
    max_attempts = (
        1
        if config.initial_constraints.mode == "unrestricted"
        else config.initial_constraints.max_attempts
    )
    attempts: list[ConstraintAttemptRecord] = []
    for attempt in range(max_attempts):
        generated = generate_stochastic_state(
            core_config, seed, candidate_index, config_sha256, attempt=attempt,
            physics_version=(config.version if config.version in {"0.4.2", "0.4.3"}
                             else "0.4.0"),
            planet_rotation=config.planet_rotation,
            atmosphere_prior=config.atmosphere_prior,
        )
        state = generated.state.model_copy(update={"generator_version": config.version})
        metrics = _constraint_metrics(
            state, generated.metadata.selected_planet_host, len(generated.initial_contacts)
        )
        reasons = (
            ()
            if config.initial_constraints.mode == "unrestricted"
            else _constraint_reasons(config, metrics)
        )
        accepted = not reasons
        attempts.append(
            ConstraintAttemptRecord(
                attempt=attempt,
                accepted=accepted,
                reason_codes=reasons,
                metrics=metrics,
            )
        )
        if accepted:
            metadata = GenerationMetadataV041(
                schema_version=config.version,
                generator_version=config.version,
                distribution_schema_version=config.version,
                world_id=state.world_id,
                seed=seed,
                candidate_index=candidate_index,
                constraint_mode=config.initial_constraints.mode,
                constraints=config.initial_constraints.model_dump(mode="json"),
                selected_attempt=attempt,
                attempt_count=attempt + 1,
                attempts=tuple(attempts),
                sampled_physical=generated.metadata.sampled_physical,
                selected_planet_host=generated.metadata.selected_planet_host,
                conics=generated.metadata.conics,
            )
            return GeneratedStochasticState(state, metadata, generated.initial_contacts)

    raise ConstraintExhausted(
        {
            "schema_version": config.version,
            "generator_id": "stochastic_conics",
            "generator_version": config.version,
            "world_id": f"candidate-{candidate_index:06d}",
            "seed": seed,
            "candidate_index": candidate_index,
            "status": "INVALID",
            "reason_code": "GENERATION_CONSTRAINT_EXHAUSTED",
            "constraint_mode": config.initial_constraints.mode,
            "constraints": config.initial_constraints.model_dump(mode="json"),
            "attempt_count": max_attempts,
            "attempts": [record.model_dump(mode="json") for record in attempts],
            "scientific_claim": (
                "bounded initial-state preference exhausted; no stability or binding claim"
            ),
        }
    )
