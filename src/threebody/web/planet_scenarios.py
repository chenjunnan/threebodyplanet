"""Versioned, read-only parameter experiments; not a climate/geology prediction."""
from __future__ import annotations

import math
import platform
from pathlib import Path
from typing import Annotated

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from threebody.common.json import sha256_json
from threebody.physics.longrun.reader import SegmentedTrajectoryReader
from threebody.web.science import query_body_metadata

G = 6.67430e-11
SIGMA = 5.670374419e-8
GAS_R = 8.314462618
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
    surface_water_kg: Finite = Field(default=1.4e21, ge=0, le=1e26)
    relief_m: Finite = Field(default=6000., gt=0, le=1e5)
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
        if self.oxygen_mole_fraction+self.water_vapor_mole_fraction > 1:
            raise ValueError('gas mole fractions exceed one')
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


def water_phase_proxy(temperature: float, pressure: float) -> str:
    if temperature < 273.16:
        return 'ice'
    if temperature >= 373.15:
        return 'unknown_high_temperature'
    saturation = 611.657*math.exp(2.5e6/461.5*(1/273.16-1/temperature))
    return 'liquid_possible' if pressure >= saturation else 'vapor_or_unconfined'


def terrain_modes(seed: int) -> list[dict]:
    rng = np.random.Generator(np.random.PCG64(seed))
    modes = []
    for octave in range(9):
        direction = rng.normal(size=3)
        direction /= np.linalg.norm(direction)
        modes.append({'direction': direction.tolist(), 'phase': rng.uniform(-math.pi, math.pi),
                      'frequency': 3.*2**octave,
                      'amplitude': .65**octave/sum(.65**i for i in range(9))})
    return modes


def terrain_height(normals: np.ndarray, seed: int, relief: float) -> np.ndarray:
    """Continuous seeded spherical noise; visual terrain, no plate tectonics."""
    heights = np.zeros(normals.shape[:-1])
    for mode in terrain_modes(seed):
        heights += np.sin(normals @ np.asarray(mode['direction'])*mode['frequency']+
                          mode['phase'])*mode['amplitude']
    return relief*heights


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
    pressure = gravity*scenario.atmosphere_mass_kg/(4*math.pi*radius**2)
    xyz = {b.id: np.asarray(b.position_m) for b in sample.bodies}
    stars = []
    for star in metadata['bodies'][:3]:
        luminosity = star['luminosity_w']
        if luminosity is None:
            raise ValueError('verified stellar luminosity missing; radiation cannot be invented')
        delta = xyz[star['id']]-xyz['planet-1']
        distance = float(np.linalg.norm(delta))
        direction = delta/distance
        stars.append({'id': star['id'], 'direction': direction.tolist(),
                      'distance_m': distance, 'radius_m': star['radius_m'],
                      'flux_w_m2': luminosity/(4*math.pi*distance**2),
                      'declination_rad': math.asin(float(np.clip(direction@axis, -1., 1.)))})
    bands = []
    for i in range(36):
        latitude = math.asin(-1+(2*i+1)/36)
        flux = sum(daily_mean_flux(latitude, s['flux_w_m2'], s['declination_rad'])
                   for s in stars)
        temperature = equilibrium_temperature((1-scenario.albedo)*flux, scenario)
        bands.append({'latitude_rad': latitude, 'temperature_k': temperature,
                      'diurnal_mean_flux_w_m2': flux})
    for i, band in enumerate(bands):
        lo, hi = max(0, i-1), min(len(bands)-1, i+1)
        gradient = (bands[hi]['temperature_k']-bands[lo]['temperature_k'])/(
            radius*(bands[hi]['latitude_rad']-bands[lo]['latitude_rad']))
        coriolis = 2*omega*math.sin(band['latitude_rad'])
        # Order-of-magnitude geostrophic scale, not a surface wind forecast.
        speed = (abs(GAS_R/scenario.molar_mass_kg_mol*gradient/coriolis)
                 if pressure > 0 and abs(coriolis) > 1e-8
                 and abs(band['latitude_rad']) > math.radians(5) else None)
        sound = math.sqrt(1.4*GAS_R/scenario.molar_mass_kg_mol*band['temperature_k'])
        band['geostrophic_scale_mps'] = speed if speed is not None and speed < .3*sound else None
    latitudes = np.arcsin(-1+(2*np.arange(24)+1)/24)
    longitudes = -math.pi+(2*np.arange(48)+1)*math.pi/48
    lat, lon = np.meshgrid(latitudes, longitudes, indexing='ij')
    normals = np.stack([np.cos(lat)*np.cos(lon), np.cos(lat)*np.sin(lon), np.sin(lat)], -1)
    heights = terrain_height(normals, scenario.seed, scenario.relief_m)
    area = 4*math.pi*radius**2/heights.size
    water_volume = scenario.surface_water_kg/1000
    lower, upper = float(heights.min()), float(heights.max())+water_volume/(area*heights.size)
    for _ in range(80):
        level = (lower+upper)/2
        if float(np.maximum(0., level-heights).sum())*area > water_volume:
            upper = level
        else:
            lower = level
    sea_level = (lower+upper)/2 if water_volume > 0 else float(heights.min())-1
    if water_volume > 0 and sea_level-float(heights.min()) > radius*.05:
        raise ValueError('water layer exceeds small-surface approximation')
    regions = []
    for r in range(24):
        # Zonal equilibrium values use nearest computed latitude, with explicit provenance.
        band = min(bands, key=lambda b: abs(b['latitude_rad']-latitudes[r]))
        for c in range(48):
            name = f'r{r}-c{c}'
            rate = scenario.hazard_rates_per_year.get(name)
            wet = water_volume > 0 and heights[r, c] < sea_level
            temp = band['temperature_k']
            potential_phase = water_phase_proxy(temp, pressure)
            phase = potential_phase if wet else 'dry'
            regions.append({'id': name, 'latitude_deg': float(np.degrees(latitudes[r])),
                            'longitude_deg': float(np.degrees(longitudes[c])),
                            'elevation_m': float(heights[r, c]), 'water_depth_m':
                            max(0., sea_level-float(heights[r, c])) if wet else 0.,
                            'water_phase_proxy': phase, 'potential_water_phase': potential_phase,
                            'temperature_k': temp,
                            'geostrophic_scale_mps': band['geostrophic_scale_mps'],
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
    local_height = float(terrain_height(normal, scenario.seed, scenario.relief_m))
    local_band = min(bands, key=lambda b: abs(b['latitude_rad']-phi))
    local_temp = local_band['temperature_k']
    water_visible = water_volume > 0 and water_phase_proxy(local_temp, pressure) in {
        'ice', 'liquid_possible'}
    ground_level = max(local_height, sea_level) if water_visible else local_height
    observer = xyz['planet-1']+(radius+ground_level+scenario.observer_height_m)*up
    sky = []
    for star in stars:
        delta = xyz[star['id']]-observer
        distance = float(np.linalg.norm(delta))
        direction = delta/distance
        altitude = math.asin(float(np.clip(direction@up, -1., 1.)))
        sky.append({'id': star['id'], 'altitude_deg': math.degrees(altitude),
                    'azimuth_deg': math.degrees(math.atan2(float(direction@east),
                                                         float(direction@north))) % 360,
                    'above_geometric_horizon': altitude >= 0,
                    'angular_radius_rad': math.asin(min(1., star['radius_m']/distance)),
                    'irradiance_w_m2': star['flux_w_m2']})
    mean_temp = sum(b['temperature_k'] for b in bands)/36
    scale_height = GAS_R*mean_temp/(scenario.molar_mass_kg_mol*gravity) if pressure > 0 else None
    if scale_height is not None and scale_height > radius*.1:
        raise ValueError('scale height exceeds thin-atmosphere approximation')
    parameters = scenario.model_dump(mode='json')
    source = {'initial_state_sha256': metadata['initial_state_sha256'],
              'physics_sha256': reader.manifest.hashes.physics_sha256,
              'sample_sha256': sha256_json(sample), 't_s': sample.t_s}
    runtime = {'python': platform.python_version(), 'numpy': np.__version__}
    scenario_hash = sha256_json({'runtime': runtime, 'model': 'planet-exploration-v1',
                                 'source': source, 'parameters': parameters})
    return {'schema_version': 'planet-exploration-v1', 'data_kind': 'PARAMETERIZED_SCENARIO',
            'run_id': reader.manifest.run_id, 'world_id': reader.manifest.world_id,
            'source': source, 'parameters': parameters, 'scenario_sha256': scenario_hash,
            'runtime': runtime,
            'terrain_modes': terrain_modes(scenario.seed), 'observer_ground_level_m': ground_level,
            'water_surface_visible': water_visible, 'observer_temperature_proxy_k': local_temp,
            'radius_m': radius, 'gravity_m_s2': gravity, 'rotation_parameter_q': q,
            'equatorial_effective_gravity_m_s2': gravity*(1-q),
            'pressure_pa': pressure, 'scale_height_m': scale_height,
            'oxygen_partial_pressure_pa': pressure*scenario.oxygen_mole_fraction,
            'water_vapor_partial_pressure_pa': pressure*scenario.water_vapor_mole_fraction,
            'sea_level_m': sea_level, 'terrain_grid': [24, 48], 'regions': regions,
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
                'Wind scale is invalid near equator, vacuum or high Mach number.',
                'Hazards need supplied regional rates and stationary Poisson assumptions.',
                'Sky is geometric: no atmospheric refraction, terrain occlusion or scattering.',
                'Water proxy omits salinity, circulation, evaporation and feedback.',
            ]}
