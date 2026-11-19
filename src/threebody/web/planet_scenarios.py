"""Versioned, read-only parameter experiments; not a climate/geology prediction."""
from __future__ import annotations

import math
import platform
from pathlib import Path
from typing import Annotated

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from threebody import __version__
from threebody.common.json import sha256_json
from threebody.physics.longrun.reader import SegmentedTrajectoryReader
from threebody.web.science import query_body_metadata

G = 6.67430e-11
SIGMA = 5.670374419e-8
GAS_R = 8.314462618
BOLTZMANN = 1.380649e-23
WATER_MOLAR_MASS_KG_MOL = 0.01801528
WATER_CRITICAL_TEMPERATURE_K = 647.096
WATER_CRITICAL_PRESSURE_PA = 22.064e6
Finite = Annotated[float, Field(allow_inf_nan=False)]
Rate = Annotated[float, Field(ge=0, le=1e6, allow_inf_nan=False)]


class PlanetScenario(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    t_s: Finite = Field(ge=0)
    seed: int = Field(default=42, ge=0, le=2**32-1)
    albedo: Finite = Field(default=.3, ge=0, le=1)
    emissivity: Finite = Field(default=1., gt=0, le=1)
    optical_depth: Finite = Field(default=0., ge=0, le=20)
    core_temperature_k: Finite = Field(default=4000., ge=0, le=20000)
    mantle_thickness_m: Finite = Field(default=2.9e6, ge=1)
    conductivity_w_m_k: Finite = Field(default=3., ge=0, le=100)
    atmosphere_mass_kg: Finite = Field(default=5.15e18, ge=0, le=1e26)
    molar_mass_kg_mol: Finite = Field(default=.029, gt=.001, le=.3)
    oxygen_mole_fraction: Finite = Field(default=0., ge=0, le=1)
    water_vapor_mole_fraction: Finite = Field(default=0., ge=0, le=1)
    carbon_dioxide_mole_fraction: Finite = Field(default=0., ge=0, le=1)
    argon_mole_fraction: Finite = Field(default=0., ge=0, le=1)
    surface_water_kg: Finite = Field(default=1.4e21, ge=0, le=1e26)
    relief_m: Finite = Field(default=18000., gt=0, le=1e5)
    terrain_shape_exponent: Finite = Field(default=.72, ge=.5, le=1.5)
    contour_level_count: int = Field(default=10, ge=10, le=10)
    terrain_vertical_exaggeration: Finite = Field(default=4.0, ge=1.0, le=8.0)
    wind_range_fraction_min: Finite = Field(default=.25, ge=0, le=1)
    wind_range_fraction_max: Finite = Field(default=.9, gt=0, le=1.5)
    rotation_rate_rad_s: Finite | None = Field(default=None, ge=0, le=1)
    rotation_axis: tuple[Finite, Finite, Finite] | None = None
    longitude_phase_rad: Finite = 0.
    latitude_deg: Finite = Field(default=0., ge=-89, le=89)
    longitude_deg: Finite = Field(default=0., ge=-180, le=180)
    observer_height_m: Finite = Field(default=2., ge=1, le=1e5)
    horizon_years: Finite = Field(default=1., gt=0, le=1000)
    hazard_definition: str | None = Field(default=None, min_length=1, max_length=200)
    hazard_rates_per_year: dict[str, Rate] = Field(default_factory=dict, max_length=1152)

    @model_validator(mode='after')
    def validate_composition(self):
        if sum((self.oxygen_mole_fraction, self.water_vapor_mole_fraction,
                self.carbon_dioxide_mole_fraction, self.argon_mole_fraction)) > 1:
            raise ValueError('gas mole fractions exceed one')
        if self.wind_range_fraction_min >= self.wind_range_fraction_max:
            raise ValueError('wind range fractions must be increasing')
        if self.rotation_axis is not None and not math.isclose(
            math.sqrt(sum(x*x for x in self.rotation_axis)), 1., abs_tol=1e-6
        ):
            raise ValueError('rotation axis must be a unit vector')
        ids = {f'r{r}-c{c}' for r in range(24) for c in range(48)}
        if self.hazard_rates_per_year and not self.hazard_definition:
            raise ValueError('hazard rates require an explicit event definition')
        if not set(self.hazard_rates_per_year) <= ids:
            raise ValueError('unknown region ID in hazard rates')
        return self


def equilibrium_temperature(absorbed: float, scenario: PlanetScenario) -> float:
    """Balance absorbed flux + static conductive core flux against grey emission."""
    k = scenario.conductivity_w_m_k/scenario.mantle_thickness_m
    epsilon = scenario.emissivity/(1+.75*scenario.optical_depth)
    low, high = 0., max(1., scenario.core_temperature_k,
                        (absorbed/(epsilon*SIGMA))**.25+1)
    for _ in range(100):
        middle = (low+high)/2
        residual = epsilon*SIGMA*middle**4-absorbed-max(
            0., k*(scenario.core_temperature_k-middle)
        )
        if residual > 0:
            high = middle
        else:
            low = middle
    return (low+high)/2


def daily_mean_flux(latitude: float, star_flux: float, declination: float) -> float:
    """Diurnal mean of point-source illumination, including polar day/night."""
    a = math.sin(latitude)*math.sin(declination)
    b = math.cos(latitude)*math.cos(declination)
    if b < 1e-14:
        return star_flux*max(0., a)
    h = math.acos(max(-1., min(1., -a/b)))
    return star_flux*(h*a+b*math.sin(h))/math.pi


def water_phase_proxy(temperature: float, water_partial_pressure_pa: float) -> str:
    """Classify an idealized local water phase from T and H2O partial pressure.

    The result is an equilibrium proxy, not a hydrology or cloud calculation.
    In particular, total atmospheric pressure is not substituted for water
    fugacity: dry background gas alone cannot make condensed water appear.
    """
    if (not math.isfinite(temperature) or temperature <= 0.0
            or not math.isfinite(water_partial_pressure_pa)
            or water_partial_pressure_pa < 0.0):
        raise ValueError('water phase inputs must be finite and physical')
    if temperature >= WATER_CRITICAL_TEMPERATURE_K:
        return (
            'supercritical_water_envelope'
            if water_partial_pressure_pa >= WATER_CRITICAL_PRESSURE_PA
            else 'steam_atmosphere'
        )
    saturation = water_saturation_pressure_pa(temperature)
    if water_partial_pressure_pa < saturation:
        return 'steam_atmosphere'
    return 'ice' if temperature < 273.16 else 'liquid_possible'


def water_saturation_pressure_pa(temperature_k: float) -> float:
    """Ice sublimation or liquid saturation pressure, capped at criticality."""
    if not math.isfinite(temperature_k) or temperature_k <= 0.0:
        raise ValueError('water saturation temperature must be finite and positive')
    if temperature_k >= WATER_CRITICAL_TEMPERATURE_K:
        return WATER_CRITICAL_PRESSURE_PA
    if temperature_k < 273.16:
        # Murphy & Koop (2005) ice-vapour expression, in Pa. This branch
        # deliberately models sublimation rather than a fixed freezing rule.
        return math.exp(
            9.550426 - 5723.265 / temperature_k
            + 3.53068 * math.log(temperature_k) - 0.00728332 * temperature_k
        )
    tau = 1.0 - temperature_k / WATER_CRITICAL_TEMPERATURE_K
    coefficients = (-7.85951783, 1.84408259, -11.7866497,
                    22.6807411, -15.9618719, 1.80122502)
    powers = (1.0, 1.5, 3.0, 3.5, 4.0, 7.5)
    exponent = WATER_CRITICAL_TEMPERATURE_K / temperature_k * sum(
        coefficient * tau**power
        for coefficient, power in zip(coefficients, powers, strict=True)
    )
    return WATER_CRITICAL_PRESSURE_PA * math.exp(exponent)


def partition_water_inventory(
    *, total_surface_inventory_kg: float, global_temperature_k: float,
    area_m2: float, gravity_m_s2: float, base_atmospheric_water_kg: float,
) -> tuple[float, float, float]:
    """Return surface, transferred-atmosphere and saturation-pressure inventories.

    This is a global well-mixed equilibrium parameterization, not hydrology.
    Above the critical temperature there is no liquid surface reservoir.
    """
    if total_surface_inventory_kg <= 0.0:
        return 0.0, 0.0, water_saturation_pressure_pa(global_temperature_k)
    saturation = water_saturation_pressure_pa(global_temperature_k)
    if global_temperature_k >= WATER_CRITICAL_TEMPERATURE_K:
        transferred = total_surface_inventory_kg
    else:
        atmospheric_capacity = max(
            0.0, saturation * area_m2 / gravity_m_s2 - base_atmospheric_water_kg
        )
        transferred = min(total_surface_inventory_kg, atmospheric_capacity)
    return total_surface_inventory_kg - transferred, transferred, saturation


def terrain_modes(seed: int) -> list[dict]:
    rng = np.random.Generator(np.random.PCG64(seed))
    modes = []
    normalizer = sum(.62**i for i in range(12))
    for octave in range(12):
        direction = rng.normal(size=3)
        direction /= np.linalg.norm(direction)
        modes.append({'direction': direction.tolist(), 'phase': rng.uniform(-math.pi, math.pi),
                      'frequency': 2.4*1.82**octave,
                      'amplitude': .62**octave/normalizer})
    return modes


def _random_unit_vector(rng: np.random.Generator) -> np.ndarray:
    vector = rng.normal(size=3)
    return vector / np.linalg.norm(vector)


def environment_terrain_model(
    seed: int, *, reference_temperature_k: float, reference_pressure_pa: float,
    rotation_parameter_q: float, surface_water_mass_fraction: float,
) -> dict:
    """Create a deterministic morphology prior from persistent environment drivers.

    The drivers constrain erosion/fracture *priors*; they cannot prove tectonic
    history. Features remain fixed across trajectory playback because the
    reference temperature is derived from the initial archived configuration.
    """
    atmosphere = min(1.0, math.log1p(reference_pressure_pa / 1000.0) / math.log(102.325))
    thermal_stress = min(1.0, abs(reference_temperature_k - 288.0) / 500.0)
    rotation_stress = min(1.0, rotation_parameter_q / 0.1)
    temperate_water = math.exp(-((reference_temperature_k - 288.0) / 85.0) ** 2)
    water = min(1.0, surface_water_mass_fraction / 0.001)
    erosion = min(1.0, 0.08 + 0.38 * atmosphere + 0.34 * water * temperate_water)
    fracture = min(1.0, 0.22 + 0.46 * thermal_stress + 0.22 * rotation_stress)
    rng = np.random.Generator(np.random.PCG64(
        np.random.SeedSequence([seed, 0x4D4F5250])
    ))

    plains = []
    for _ in range(2 + int(erosion > 0.62)):
        plains.append({
            'center_unit': _random_unit_vector(rng).tolist(),
            'angular_radius_rad': float(rng.uniform(.20, .48)),
            'inner_fraction': float(rng.uniform(.42, .62)),
            'strength': float(rng.uniform(.62, .86) * (.72 + .28 * erosion)),
            'target_normalized_height': float(rng.uniform(-.24, .12)),
        })

    def linear_feature() -> tuple[np.ndarray, np.ndarray]:
        center = _random_unit_vector(rng)
        trial = _random_unit_vector(rng)
        normal = np.cross(center, trial)
        normal /= np.linalg.norm(normal)
        return center, normal

    ridges = []
    for _ in range(4 + int(fracture > .42) + int(fracture > .72)):
        center, normal = linear_feature()
        ridges.append({
            'center_unit': center.tolist(), 'great_circle_normal_unit': normal.tolist(),
            'half_width_rad': float(rng.uniform(.010, .038)),
            'extent_rad': float(rng.uniform(.28, .82)),
            'amplitude_fraction': float(rng.uniform(.20, .36) * (1.12 - .36 * erosion)),
            'profile_exponent': float(rng.uniform(.48, .72)),
            'crest_frequency': float(rng.uniform(180.0, 520.0)),
            'crest_phase_rad': float(rng.uniform(-math.pi, math.pi)),
            'gully_frequency': float(rng.uniform(650.0, 1500.0)),
            'gully_phase_rad': float(rng.uniform(-math.pi, math.pi)),
            'gully_depth_fraction': float(rng.uniform(.018, .052) * (1.08 - .45 * erosion)),
        })

    rugged_modes = []
    ruggedness = .18 + .72 * (1.0 - erosion) + .25 * fracture
    for octave in range(7):
        rugged_modes.append({
            'direction': _random_unit_vector(rng).tolist(),
            'phase': float(rng.uniform(-math.pi, math.pi)),
            'frequency': float(900.0 * 1.62**octave * rng.uniform(.88, 1.12)),
            'amplitude_fraction': float(
                min(1.0, ruggedness) * .020 * .66**octave
            ),
        })

    faults = []
    for _ in range(2 + int(fracture > .48) + int(fracture > .78)):
        center, normal = linear_feature()
        faults.append({
            'center_unit': center.tolist(), 'great_circle_normal_unit': normal.tolist(),
            'half_width_rad': float(rng.uniform(.006, .022)),
            'extent_rad': float(rng.uniform(.38, .92)),
            'canyon_depth_fraction': float(rng.uniform(.10, .22) * (.65 + .55 * fracture)),
            'scarp_height_fraction': float(rng.uniform(.045, .12) * (.65 + .55 * fracture)),
        })
    return {
        'model': 'environment_morphology_proxy_v2',
        'base_scale': .56,
        'drivers': {
            'reference_temperature_k': reference_temperature_k,
            'reference_pressure_pa': reference_pressure_pa,
            'rotation_parameter_q': rotation_parameter_q,
            'surface_water_mass_fraction': surface_water_mass_fraction,
            'erosion_index': erosion,
            'fracture_index': fracture,
        },
        'plains': plains, 'ridges': ridges, 'faults': faults,
        'rugged_modes': rugged_modes,
        'semantics': (
            'SEEDED_ENVIRONMENT_CONSTRAINED_MORPHOLOGY_PRIOR_NOT_TECTONIC_INFERENCE'
        ),
    }


def terrain_height(
    normals: np.ndarray, seed: int, relief: float, shape_exponent: float = .72
) -> np.ndarray:
    """Continuous seeded spherical noise; visual terrain, no plate tectonics."""
    heights = np.zeros(normals.shape[:-1])
    for mode in terrain_modes(seed):
        heights += np.sin(normals @ np.asarray(mode['direction'])*mode['frequency']+
                          mode['phase'])*mode['amplitude']
    shaped = np.sign(heights) * np.abs(heights) ** shape_exponent
    return relief*shaped


def environment_terrain_height(
    normals: np.ndarray, modes: list[dict], model: dict, relief: float,
    shape_exponent: float,
) -> np.ndarray:
    """Evaluate the shared spherical morphology used by map, water and WebGL."""
    normals = np.asarray(normals, dtype=float)
    raw = np.zeros(normals.shape[:-1])
    for mode in modes:
        raw += np.sin(
            normals @ np.asarray(mode['direction']) * mode['frequency'] + mode['phase']
        ) * mode['amplitude']
    height = model['base_scale'] * np.sign(raw) * np.abs(raw) ** shape_exponent

    for mode in model.get('rugged_modes', []):
        height += mode['amplitude_fraction'] * np.sin(
            normals @ np.asarray(mode['direction']) * mode['frequency'] + mode['phase']
        )

    for plain in model['plains']:
        angle = np.arccos(np.clip(normals @ np.asarray(plain['center_unit']), -1.0, 1.0))
        radius = plain['angular_radius_rad']
        inner = radius * plain['inner_fraction']
        weight = np.clip((radius - angle) / (radius - inner), 0.0, 1.0)
        weight = weight * weight * (3.0 - 2.0 * weight) * plain['strength']
        height = height * (1.0 - weight) + plain['target_normalized_height'] * weight

    for ridge in model['ridges']:
        normal = np.asarray(ridge['great_circle_normal_unit'])
        signed = np.arcsin(np.clip(normals @ normal, -1.0, 1.0))
        along = np.arccos(np.clip(normals @ np.asarray(ridge['center_unit']), -1.0, 1.0))
        localization = np.exp(-(along / ridge['extent_rad']) ** 4)
        crest_wave = .5 + .5 * np.sin(
            along * ridge['crest_frequency'] + ridge['crest_phase_rad']
        )
        crest = .48 + .52 * np.maximum(0.0, crest_wave) ** .32
        profile = np.exp(-(
            np.abs(signed) / ridge['half_width_rad']
        ) ** ridge['profile_exponent'])
        gullies = ridge['gully_depth_fraction'] * np.maximum(0.0, np.sin(
            along * ridge['gully_frequency'] + ridge['gully_phase_rad']
        )) ** 3
        height += localization * profile * (
            ridge['amplitude_fraction'] * crest - gullies
        )

    for fault in model['faults']:
        normal = np.asarray(fault['great_circle_normal_unit'])
        signed = np.arcsin(np.clip(normals @ normal, -1.0, 1.0))
        along = np.arccos(np.clip(normals @ np.asarray(fault['center_unit']), -1.0, 1.0))
        localization = np.exp(-(along / fault['extent_rad']) ** 4)
        width = fault['half_width_rad']
        height += localization * (
            fault['scarp_height_fraction'] * np.tanh(signed / (width * .38))
            - fault['canyon_depth_fraction'] * np.exp(-(signed / width) ** 2)
        )
    return relief * np.clip(height, -1.0, 1.0)


def _planck_rgb(temperature_k: float) -> list[float]:
    values = []
    for wavelength_nm in (680.0, 550.0, 440.0):
        wavelength_m = wavelength_nm * 1.0e-9
        exponent = 6.62607015e-34 * 299792458.0 / (
            wavelength_m * BOLTZMANN * temperature_k
        )
        values.append(1.0 / (wavelength_m**5 * math.expm1(min(700.0, exponent))))
    maximum = max(values)
    return [value / maximum for value in values]


def _atmospheric_optics(pressure_pa: float, composition: dict[str, float]) -> dict:
    if pressure_pa <= 0.0:
        return {
            'model': 'vacuum', 'optical_depth_rgb': [0.0, 0.0, 0.0],
            'composition_mole_fraction': composition,
        }
    rayleigh_factor = sum(composition[name] * factor for name, factor in {
        'nitrogen': 1.0, 'oxygen': 1.08, 'carbon_dioxide': 1.45,
        'argon': .94, 'water_vapor': .62,
    }.items())
    pressure_ratio = pressure_pa / 101325.0
    depths = []
    for wavelength_nm in (680.0, 550.0, 440.0):
        rayleigh = .10 * pressure_ratio * rayleigh_factor * (550.0 / wavelength_nm) ** 4
        # Three broad visible-band absorption proxies; not line-by-line spectroscopy.
        absorption = pressure_ratio * (
            composition['water_vapor'] * ({680.: .035, 550.: .004, 440.: .001}[wavelength_nm])
            + composition['oxygen'] * ({680.: .008, 550.: .001, 440.: .002}[wavelength_nm])
            + composition['carbon_dioxide'] * .001
        )
        depths.append(rayleigh + absorption)
    return {
        'model': 'three_band_rayleigh_absorption_proxy_v1',
        'wavelength_nm_rgb': [680.0, 550.0, 440.0],
        'optical_depth_rgb': depths,
        'composition_mole_fraction': composition,
    }


def _air_mass(altitude_rad: float) -> float | None:
    if altitude_rad < 0.0:
        return None
    altitude_deg = math.degrees(altitude_rad)
    return 1.0 / (
        math.sin(altitude_rad) + .50572 * (altitude_deg + 6.07995) ** -1.6364
    )


def build_scenario(directory: Path, scenario: PlanetScenario) -> dict:
    reader = SegmentedTrajectoryReader(directory)
    sample = next(reader.iter_samples(start_s=scenario.t_s, end_s=scenario.t_s), None)
    if sample is None:
        raise ValueError('choose an exact committed trajectory sample; no extrapolation')
    if any(e.event_type == 'COLLISION' and e.t_s <= sample.t_s
           for e in reader.iter_events()):
        raise ValueError('surface scenario unavailable at or after physical collision')
    metadata = query_body_metadata(directory)
    planet = metadata['bodies'][3]
    mass, radius = planet['mass_kg'], planet['radius_m']
    if scenario.mantle_thickness_m > radius:
        raise ValueError('mantle thickness exceeds planet radius')
    if scenario.relief_m > radius*.05:
        raise ValueError('relief exceeds small-topography model limit (5% radius)')
    if scenario.atmosphere_mass_kg+scenario.surface_water_kg > mass*.05:
        raise ValueError('volatile inventory exceeds thin-envelope model limit (5% mass)')
    omega = scenario.rotation_rate_rad_s
    if omega is None:
        omega = planet['rotation_rate_rad_s']
    axis = scenario.rotation_axis or planet['rotation_axis_inertial_unit']
    if omega is None or axis is None:
        raise ValueError('rotation rate/axis unavailable: supply explicit scenario assumptions')
    axis = np.asarray(axis, dtype=float)
    if axis.shape != (3,) or not np.all(np.isfinite(axis)) or not math.isclose(
        float(np.linalg.norm(axis)), 1., abs_tol=1e-6
    ):
        raise ValueError('archived rotation axis must be a finite unit vector')
    if isinstance(omega, bool) or not math.isfinite(omega) or omega < 0:
        raise ValueError('archived rotation rate invalid; supply an explicit scenario value')
    axis /= np.linalg.norm(axis)
    gravity = G*mass/radius**2
    q = omega**2*radius/gravity
    if q >= .1:
        raise ValueError('rotation outside spherical, slow-rotation approximation (q < 0.1)')
    if scenario.atmosphere_mass_kg == 0 and scenario.optical_depth != 0:
        raise ValueError('a vacuum cannot have atmospheric optical depth')
    area_m2 = 4*math.pi*radius**2
    base_composition = {
        'oxygen': scenario.oxygen_mole_fraction,
        'water_vapor': scenario.water_vapor_mole_fraction,
        'carbon_dioxide': scenario.carbon_dioxide_mole_fraction,
        'argon': scenario.argon_mole_fraction,
    }
    base_composition['nitrogen'] = 1.0 - sum(base_composition.values())
    xyz = {b.id: np.asarray(b.position_m) for b in sample.bodies}
    stars = []
    for star in metadata['bodies'][:3]:
        luminosity = star['luminosity_w']
        if luminosity is None:
            raise ValueError('verified stellar luminosity missing; radiation cannot be invented')
        delta = xyz[star['id']]-xyz['planet-1']
        distance = float(np.linalg.norm(delta))
        direction = delta/distance
        effective_temperature = (
            luminosity / (4*math.pi*star['radius_m']**2*SIGMA)
        ) ** .25
        stars.append({'id': star['id'], 'direction': direction.tolist(),
                      'distance_m': distance, 'radius_m': star['radius_m'],
                      'flux_w_m2': luminosity/(4*math.pi*distance**2),
                      'effective_temperature_k': effective_temperature,
                      'blackbody_rgb': _planck_rgb(effective_temperature),
                      'declination_rad': math.asin(float(np.clip(direction@axis, -1., 1.)))})
    bands = []
    for i in range(36):
        latitude = math.asin(-1+(2*i+1)/36)
        flux = sum(daily_mean_flux(latitude, s['flux_w_m2'], s['declination_rad'])
                   for s in stars)
        temperature = equilibrium_temperature((1-scenario.albedo)*flux, scenario)
        bands.append({'latitude_rad': latitude, 'temperature_k': temperature,
                      'diurnal_mean_flux_w_m2': flux})
    mean_band_temperature = sum(b['temperature_k'] for b in bands) / len(bands)
    initial_sample = next(reader.iter_samples(), None)
    if initial_sample is None:
        raise ValueError('terrain reference requires the initial committed sample')
    initial_xyz = {body.id: np.asarray(body.position_m) for body in initial_sample.bodies}
    reference_flux = 0.0
    for star in metadata['bodies'][:3]:
        separation = float(np.linalg.norm(
            initial_xyz[star['id']] - initial_xyz['planet-1']
        ))
        reference_flux += star['luminosity_w'] / (4 * math.pi * separation**2)
    terrain_reference_temperature = equilibrium_temperature(
        (1.0 - scenario.albedo) * reference_flux / 4.0, scenario
    )
    terrain_reference_pressure = gravity * scenario.atmosphere_mass_kg / area_m2
    morphology = environment_terrain_model(
        scenario.seed,
        reference_temperature_k=terrain_reference_temperature,
        reference_pressure_pa=terrain_reference_pressure,
        rotation_parameter_q=q,
        surface_water_mass_fraction=scenario.surface_water_kg / mass,
    )
    modes = terrain_modes(scenario.seed)
    base_moles = (
        scenario.atmosphere_mass_kg / scenario.molar_mass_kg_mol
        if scenario.atmosphere_mass_kg > 0.0 else 0.0
    )
    base_water_mass = (
        base_moles * base_composition['water_vapor'] * WATER_MOLAR_MASS_KG_MOL
    )
    surface_water_kg, transferred_water_kg, saturation_pressure = (
        partition_water_inventory(
            total_surface_inventory_kg=scenario.surface_water_kg,
            global_temperature_k=mean_band_temperature,
            area_m2=area_m2, gravity_m_s2=gravity,
            base_atmospheric_water_kg=base_water_mass,
        )
    )
    component_moles = {
        name: base_moles * fraction for name, fraction in base_composition.items()
    }
    component_moles['water_vapor'] += (
        transferred_water_kg / WATER_MOLAR_MASS_KG_MOL
    )
    total_moles = sum(component_moles.values())
    composition = (
        {name: value / total_moles for name, value in component_moles.items()}
        if total_moles > 0.0 else base_composition
    )
    atmosphere_mass_kg = scenario.atmosphere_mass_kg + transferred_water_kg
    effective_molar_mass = (
        atmosphere_mass_kg / total_moles
        if total_moles > 0.0 else scenario.molar_mass_kg_mol
    )
    pressure = gravity * atmosphere_mass_kg / area_m2
    optics = _atmospheric_optics(pressure, composition)
    water_partial_pressure = pressure * composition['water_vapor']
    if scenario.surface_water_kg == 0.0:
        inventory_state = 'NO_SEPARATE_SURFACE_WATER_INVENTORY'
    elif mean_band_temperature >= WATER_CRITICAL_TEMPERATURE_K:
        inventory_state = (
            'SUPERCRITICAL_WATER_ENVELOPE'
            if water_partial_pressure >= WATER_CRITICAL_PRESSURE_PA
            else 'STEAM_ATMOSPHERE'
        )
    elif surface_water_kg == 0.0:
        inventory_state = 'STEAM_ATMOSPHERE'
    elif mean_band_temperature < 273.16:
        inventory_state = 'SURFACE_ICE_WITH_SATURATED_VAPOR'
    else:
        inventory_state = 'CONDENSED_SURFACE_WATER_WITH_SATURATED_VAPOR'
    for i, band in enumerate(bands):
        lo, hi = max(0, i-1), min(len(bands)-1, i+1)
        gradient = (bands[hi]['temperature_k']-bands[lo]['temperature_k'])/(
            radius*(bands[hi]['latitude_rad']-bands[lo]['latitude_rad']))
        coriolis = 2*omega*math.sin(band['latitude_rad'])
        # Order-of-magnitude geostrophic scale, not a surface wind forecast.
        speed = (abs(GAS_R/effective_molar_mass*gradient/coriolis)
                 if pressure > 0 and abs(coriolis) > 1e-8
                 and abs(band['latitude_rad']) > math.radians(5) else None)
        sound = math.sqrt(1.4*GAS_R/effective_molar_mass*band['temperature_k'])
        band['geostrophic_scale_mps'] = speed if speed is not None and speed < .3*sound else None
        thermal_scale = math.sqrt(
            GAS_R/effective_molar_mass
            * abs(band['temperature_k']-mean_band_temperature)
        )
        reference = band['geostrophic_scale_mps'] or thermal_scale
        if pressure > 0.0:
            wind_rng = np.random.Generator(np.random.PCG64(
                np.random.SeedSequence([scenario.seed, 9000+i])
            ))
            factors = sorted(wind_rng.uniform(
                scenario.wind_range_fraction_min,
                scenario.wind_range_fraction_max,
                size=2,
            ))
            upper = min(reference*factors[1], .3*sound)
            lower = min(reference*factors[0], upper)
            band['wind_speed_range_mps'] = [float(lower), float(upper)]
            band['wind_range_semantics'] = (
                'SEEDED_PRIOR_BOUNDED_BY_THERMAL_OR_GEOSTROPHIC_REFERENCE'
            )
        else:
            band['wind_speed_range_mps'] = None
            band['wind_range_semantics'] = 'VACUUM_NOT_APPLICABLE'
    latitudes = np.arcsin(-1+(2*np.arange(24)+1)/24)
    longitudes = -math.pi+(2*np.arange(48)+1)*math.pi/48
    lat, lon = np.meshgrid(latitudes, longitudes, indexing='ij')
    normals = np.stack([np.cos(lat)*np.cos(lon), np.cos(lat)*np.sin(lon), np.sin(lat)], -1)
    heights = environment_terrain_height(
        normals, modes, morphology, scenario.relief_m, scenario.terrain_shape_exponent
    )
    # Determine sea level on a materially finer equal-area grid. The v6 grid
    # also resolves the finite-width fault/canyon features used by the map.
    water_latitudes = np.arcsin(-1+(2*np.arange(192)+1)/192)
    water_longitudes = -math.pi+(2*np.arange(384)+1)*math.pi/384
    water_lat, water_lon = np.meshgrid(
        water_latitudes, water_longitudes, indexing='ij'
    )
    water_normals = np.stack([
        np.cos(water_lat)*np.cos(water_lon),
        np.cos(water_lat)*np.sin(water_lon), np.sin(water_lat)
    ], -1)
    water_heights = environment_terrain_height(
        water_normals, modes, morphology, scenario.relief_m,
        scenario.terrain_shape_exponent
    )
    water_cell_area = 4*math.pi*radius**2/water_heights.size
    water_volume = surface_water_kg/1000
    lower, upper = (float(water_heights.min()),
                    float(water_heights.max())+water_volume/(water_cell_area*water_heights.size))
    for _ in range(80):
        level = (lower+upper)/2
        if float(np.maximum(0., level-water_heights).sum())*water_cell_area > water_volume:
            upper = level
        else:
            lower = level
    sea_level = ((lower+upper)/2 if water_volume > 0
                 else float(water_heights.min())-1)
    if water_volume > 0 and sea_level-float(water_heights.min()) > radius*.05:
        raise ValueError('water layer exceeds small-surface approximation')
    represented_water_volume = (
        float(np.maximum(0., sea_level-water_heights).sum())*water_cell_area
        if water_volume > 0 else 0.0
    )
    regions = []
    for r in range(24):
        # Zonal equilibrium values use nearest computed latitude, with explicit provenance.
        band = min(bands, key=lambda b: abs(b['latitude_rad']-latitudes[r]))
        for c in range(48):
            name = f'r{r}-c{c}'
            rate = scenario.hazard_rates_per_year.get(name)
            wet = water_volume > 0 and heights[r, c] < sea_level
            temp = band['temperature_k']
            potential_phase = water_phase_proxy(temp, water_partial_pressure)
            phase = potential_phase if wet else 'dry'
            regions.append({'id': name, 'latitude_deg': float(np.degrees(latitudes[r])),
                            'longitude_deg': float(np.degrees(longitudes[c])),
                            'elevation_m': float(heights[r, c]), 'water_depth_m':
                            max(0., sea_level-float(heights[r, c])) if wet else 0.,
                            'water_phase_proxy': phase, 'potential_water_phase': potential_phase,
                            'temperature_k': temp,
                            'geostrophic_scale_mps': band['geostrophic_scale_mps'],
                            'wind_speed_range_mps': band['wind_speed_range_mps'],
                            'wind_range_semantics': band['wind_range_semantics'],
                            'assumed_hazard_rate_per_year': rate,
                            'conditional_hazard_probability':
                            None if rate is None else -math.expm1(-rate*scenario.horizon_years)})
    # Local sky uses the real archived star positions and an explicit longitude-zero epoch.
    reference = np.array([1., 0., 0.]) if abs(axis[0]) < .9 else np.array([0., 1., 0.])
    e0 = reference-axis*float(reference@axis)
    e0 /= np.linalg.norm(e0)
    e1 = np.cross(axis, e0)
    phi = math.radians(scenario.latitude_deg)
    angle = math.radians(scenario.longitude_deg)+scenario.longitude_phase_rad+omega*sample.t_s
    east = -math.sin(angle)*e0+math.cos(angle)*e1
    radial = math.cos(angle)*e0+math.sin(angle)*e1
    up = math.cos(phi)*radial+math.sin(phi)*axis
    north = -math.sin(phi)*radial+math.cos(phi)*axis
    normal = np.array([math.cos(phi)*math.cos(math.radians(scenario.longitude_deg)),
                       math.cos(phi)*math.sin(math.radians(scenario.longitude_deg)), math.sin(phi)])
    local_height = float(environment_terrain_height(
        normal, modes, morphology, scenario.relief_m, scenario.terrain_shape_exponent
    ))
    local_band = min(bands, key=lambda b: abs(b['latitude_rad']-phi))
    local_temp = local_band['temperature_k']
    water_visible = (water_volume > 0 and local_height < sea_level
                     and water_phase_proxy(local_temp, water_partial_pressure) in {
        'ice', 'liquid_possible'}
    )
    ground_level = max(local_height, sea_level) if water_visible else local_height
    observer = xyz['planet-1']+(radius+ground_level+scenario.observer_height_m)*up
    sky = []
    for star in stars:
        delta = xyz[star['id']]-observer
        distance = float(np.linalg.norm(delta))
        direction = delta/distance
        altitude = math.asin(float(np.clip(direction@up, -1., 1.)))
        air_mass = _air_mass(altitude)
        transmittance = (
            [math.exp(-depth*air_mass) for depth in optics['optical_depth_rgb']]
            if air_mass is not None else [0.0, 0.0, 0.0]
        )
        spectral_weights = star['blackbody_rgb']
        visible_transmission = sum(
            spectral_weights[i]*transmittance[i] for i in range(3)
        )/sum(spectral_weights)
        sky.append({'id': star['id'], 'altitude_deg': math.degrees(altitude),
                    'azimuth_deg': math.degrees(math.atan2(float(direction@east),
                                                         float(direction@north))) % 360,
                    'above_geometric_horizon': altitude >= 0,
                    'angular_radius_rad': math.asin(min(1., star['radius_m']/distance)),
                    'top_of_atmosphere_irradiance_w_m2': star['flux_w_m2'],
                    'visible_air_mass': air_mass,
                    'visible_transmittance_rgb': transmittance,
                    'blackbody_rgb': spectral_weights,
                    'surface_direct_irradiance_proxy_w_m2': (
                        star['flux_w_m2']*visible_transmission if altitude >= 0 else 0.0
                    ),
                    # Backward-compatible name now explicitly equals surface direct proxy.
                    'irradiance_w_m2': (
                        star['flux_w_m2']*visible_transmission if altitude >= 0 else 0.0
                    )})
    scattered = [0.0, 0.0, 0.0]
    for star in sky:
        if not star['above_geometric_horizon']:
            continue
        for channel in range(3):
            scattered[channel] += (
                star['top_of_atmosphere_irradiance_w_m2']
                * star['blackbody_rgb'][channel]
                * (1.0-star['visible_transmittance_rgb'][channel])
            )
    peak = max(scattered, default=0.0)
    sky_linear_rgb = [value/peak if peak > 0.0 else 0.0 for value in scattered]
    optics['sky_scattered_linear_rgb'] = sky_linear_rgb
    optics['sky_display_srgb'] = [value**(1/2.2) for value in sky_linear_rgb]
    optics['sky_scattered_intensity_proxy_w_m2'] = sum(scattered)
    optics['display_semantics'] = 'RELATIVE_HDR_TONE_MAPPING_NOT_SI_SCREEN_LUMINANCE'
    mean_temp = sum(b['temperature_k'] for b in bands)/36
    scale_height = GAS_R*mean_temp/(effective_molar_mass*gravity) if pressure > 0 else None
    if scale_height is not None and scale_height > radius*.1:
        raise ValueError('scale height exceeds thin-atmosphere approximation')
    parameters = scenario.model_dump(mode='json')
    source = {'initial_state_sha256': metadata['initial_state_sha256'],
              'physics_sha256': reader.manifest.hashes.physics_sha256,
              'sample_sha256': sha256_json(sample), 't_s': sample.t_s}
    runtime = {
        'project': __version__, 'python': platform.python_version(), 'numpy': np.__version__
    }
    scenario_hash = sha256_json({'runtime': runtime, 'model': 'planet-exploration-v6',
                                 'source': source, 'parameters': parameters,
                                 'terrain_model': morphology})
    contour_levels = np.linspace(
        float(water_heights.min()), float(water_heights.max()),
        scenario.contour_level_count+2
    )[1:-1].tolist()
    return {'schema_version': 'planet-exploration-v6', 'data_kind': 'PARAMETERIZED_SCENARIO',
            'run_id': reader.manifest.run_id, 'world_id': reader.manifest.world_id,
            'source': source, 'parameters': parameters, 'scenario_sha256': scenario_hash,
            'runtime': runtime,
            'terrain_modes': modes, 'terrain_model': morphology,
            'observer_ground_level_m': ground_level,
            'water_surface_visible': water_visible, 'has_surface_water': water_volume > 0,
            'observer_location': {'latitude_deg': scenario.latitude_deg,
                                  'longitude_deg': scenario.longitude_deg},
            'visualization': {
                'terrain_vertical_exaggeration': scenario.terrain_vertical_exaggeration,
                'minimum_point_source_psf_px': 5,
                'background_stars_visible': not (
                    pressure >= 1000.0
                    and optics['sky_scattered_intensity_proxy_w_m2'] >= 1.0
                ),
                'semantics': 'DISPLAY_ONLY_NOT_USED_BY_PHYSICS',
            },
            'observer_temperature_proxy_k': local_temp,
            'radius_m': radius, 'gravity_m_s2': gravity, 'rotation_parameter_q': q,
            'equatorial_effective_gravity_m_s2': gravity*(1-q),
            'pressure_pa': pressure, 'scale_height_m': scale_height,
            'atmosphere_mass_kg': atmosphere_mass_kg,
            'effective_molar_mass_kg_mol': effective_molar_mass,
            'atmospheric_composition_mole_fraction': composition,
            'oxygen_partial_pressure_pa': pressure*composition['oxygen'],
            'water_vapor_partial_pressure_pa': water_partial_pressure,
            'carbon_dioxide_partial_pressure_pa': (
                pressure*composition['carbon_dioxide']
            ),
            'water_inventory': {
                'model': 'global_saturation_partition_v1',
                'total_input_surface_inventory_kg': scenario.surface_water_kg,
                'remaining_surface_water_kg': surface_water_kg,
                'transferred_to_atmosphere_kg': transferred_water_kg,
                'base_atmospheric_water_kg': base_water_mass,
                'global_equal_area_temperature_proxy_k': mean_band_temperature,
                'saturation_pressure_proxy_pa': saturation_pressure,
                'water_partial_pressure_pa': water_partial_pressure,
                'state': inventory_state,
                'semantics': 'PARAMETERIZED_GLOBAL_EQUILIBRIUM_NOT_WATER_CYCLE',
            },
            'atmospheric_optics': optics,
            'sea_level_m': sea_level, 'terrain_grid': [24, 48], 'regions': regions,
            'water_volume_diagnostic': {
                'method': 'equal_area_192x384_basin_fill',
                'target_volume_m3': water_volume,
                'represented_volume_m3': represented_water_volume,
                'relative_residual': (
                    0.0 if water_volume == 0 else
                    (represented_water_volume-water_volume)/water_volume
                ),
            },
            'contour_levels_m': contour_levels,
            'bands': bands, 'sky': sky,
            'equator_temperature_k': equilibrium_temperature((1-scenario.albedo)*sum(
                daily_mean_flux(0., s['flux_w_m2'], s['declination_rad'])
                for s in stars), scenario),
            'north_pole_temperature_k': equilibrium_temperature((1-scenario.albedo)*sum(
                daily_mean_flux(math.pi/2, s['flux_w_m2'], s['declination_rad'])
                for s in stars), scenario),
            'south_pole_temperature_k': equilibrium_temperature((1-scenario.albedo)*sum(
                daily_mean_flux(-math.pi/2, s['flux_w_m2'], s['declination_rad'])
                for s in stars), scenario),
            'limitations': [
                'Terrain is seeded procedural topography, not a tectonic prediction.',
                'Gas inventory, composition, water, grey opacity and core state are assumptions.',
                'Static grey equilibrium plus conductive core heat; not M3 history.',
                'Diurnal means require rapid rotation and thermal averaging; not weather.',
                'Wind ranges are seeded priors bounded by reference scales, not forecasts.',
                'Hazards need supplied regional rates and stationary Poisson assumptions.',
                'Sky optics use three broad visible bands; no refraction, clouds, terrain '
                'occlusion, multiple scattering or line-by-line spectra.',
                'Water proxy omits salinity, circulation, evaporation and feedback.',
                'Water inventory partition is instantaneous global saturation equilibrium, '
                'not resolved transport, clouds, precipitation or cold-trap hydrology.',
                'Terrain vertical exaggeration and decorative star suppression are visual only.',
            ]}
