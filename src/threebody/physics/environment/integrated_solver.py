from __future__ import annotations

import math
import platform
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from threebody import __version__
from threebody.common.json import sha256_json
from threebody.errors import ConfigError
from threebody.physics.environment.integrated_models import (
    GAS_MOLAR_MASS_KG_MOL,
    EnvironmentEvent,
    IntegratedCellState,
    IntegratedDiagnostics,
    IntegratedEnvironmentConfig,
    IntegratedEnvironmentResult,
    IntegratedSnapshot,
)
from threebody.physics.environment.spatial_forcing import M5ASpatialForcing
from threebody.physics.thermal.diagnostics import STEFAN_BOLTZMANN_W_M2_K4

GAS_R = 8.31446261815324
CP_AIR = 1004.0
CP_WATER = 4184.0
CP_ICE = 2100.0
LATENT_FUSION_WATER = 333_550.0
LATENT_VAPORIZATION_WATER = 2_500_000.0
WATER_CRITICAL_T_K = 647.096
WATER_CRITICAL_P_PA = 22.064e6

# cp, solidus at 1 atm, liquidus at 1 atm, pressure slope, latent heat
MATERIAL_PROPERTIES = {
    "basaltic_silicate": (1000.0, 1350.0, 1950.0, 1.2e-8, 4.0e5),
    "granitic_silicate": (900.0, 1120.0, 1650.0, 1.0e-8, 3.5e5),
    "iron_nickel": (500.0, 1720.0, 2150.0, 8.0e-9, 2.7e5),
    "sulfur_compounds": (700.0, 360.0, 460.0, 3.0e-8, 1.0e5),
}
EVENT_TYPES = (
    "FOG", "STRONG_WIND", "HEATWAVE", "COLDWAVE",
    "HEAVY_PRECIPITATION", "THUNDERSTORM_CANDIDATE",
)
EVENT_ENTER_S = np.asarray([10800.0, 10800.0, 43200.0, 43200.0, 3600.0, 7200.0])
EVENT_EXIT_S = np.asarray([7200.0, 7200.0, 21600.0, 21600.0, 3600.0, 3600.0])
EVENT_COOLDOWN_S = np.asarray([10800.0, 21600.0, 86400.0, 86400.0, 10800.0, 21600.0])


@dataclass
class IntegratedState:
    t_s: float
    step_index: int
    output_index: int
    surface_temperature_k: np.ndarray
    rock_temperature_k: np.ndarray
    ocean_temperature_k: np.ndarray
    air_temperature_k: np.ndarray
    vapor_kg_m2: np.ndarray
    supercritical_kg_m2: np.ndarray
    cloud_kg_m2: np.ndarray
    liquid_kg_m2: np.ndarray
    ice_kg_m2: np.ndarray
    runoff_kg_m2: np.ndarray
    east_wind_m_s: np.ndarray
    north_wind_m_s: np.ndarray
    event_active: np.ndarray
    event_enter_s: np.ndarray
    event_exit_s: np.ndarray
    event_cooldown_s: np.ndarray
    active_event_index: np.ndarray
    events: list[dict[str, Any]] = field(default_factory=list)
    integrated_external_energy_j: float = 0.0
    initial_total_energy_j: float = 0.0
    wind_limit_hits: int = 0
    maximum_scaled_energy_residual: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "t_s": self.t_s, "step_index": self.step_index,
            "output_index": self.output_index,
            "surface_temperature_k": self.surface_temperature_k.tolist(),
            "rock_temperature_k": self.rock_temperature_k.tolist(),
            "ocean_temperature_k": self.ocean_temperature_k.tolist(),
            "air_temperature_k": self.air_temperature_k.tolist(),
            "vapor_kg_m2": self.vapor_kg_m2.tolist(),
            "supercritical_kg_m2": self.supercritical_kg_m2.tolist(),
            "cloud_kg_m2": self.cloud_kg_m2.tolist(),
            "liquid_kg_m2": self.liquid_kg_m2.tolist(),
            "ice_kg_m2": self.ice_kg_m2.tolist(),
            "runoff_kg_m2": self.runoff_kg_m2.tolist(),
            "east_wind_m_s": self.east_wind_m_s.tolist(),
            "north_wind_m_s": self.north_wind_m_s.tolist(),
            "event_active": self.event_active.tolist(),
            "event_enter_s": self.event_enter_s.tolist(),
            "event_exit_s": self.event_exit_s.tolist(),
            "event_cooldown_s": self.event_cooldown_s.tolist(),
            "active_event_index": self.active_event_index.tolist(),
            "events": self.events,
            "integrated_external_energy_j": self.integrated_external_energy_j,
            "initial_total_energy_j": self.initial_total_energy_j,
            "wind_limit_hits": self.wind_limit_hits,
            "maximum_scaled_energy_residual": self.maximum_scaled_energy_residual,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any], cell_count: int) -> IntegratedState:
        scalar_arrays = (
            "surface_temperature_k", "rock_temperature_k", "ocean_temperature_k",
            "air_temperature_k", "vapor_kg_m2", "cloud_kg_m2", "liquid_kg_m2",
            "supercritical_kg_m2", "ice_kg_m2", "runoff_kg_m2",
            "east_wind_m_s", "north_wind_m_s",
        )
        values = {}
        for name in scalar_arrays:
            array = np.asarray(raw[name], dtype=float)
            if array.shape != (cell_count,) or not np.all(np.isfinite(array)):
                raise ConfigError(f"invalid checkpoint array {name}")
            values[name] = array
        matrix_arrays = {
            "event_active": bool, "event_enter_s": float, "event_exit_s": float,
            "event_cooldown_s": float, "active_event_index": int,
        }
        for name, dtype in matrix_arrays.items():
            array = np.asarray(raw[name], dtype=dtype)
            if array.shape != (len(EVENT_TYPES), cell_count):
                raise ConfigError(f"invalid checkpoint array {name}")
            values[name] = array
        return cls(
            t_s=float(raw["t_s"]), step_index=int(raw["step_index"]),
            output_index=int(raw["output_index"]), events=list(raw["events"]),
            integrated_external_energy_j=float(raw["integrated_external_energy_j"]),
            initial_total_energy_j=float(raw["initial_total_energy_j"]),
            wind_limit_hits=int(raw["wind_limit_hits"]),
            maximum_scaled_energy_residual=float(raw["maximum_scaled_energy_residual"]),
            **values,
        )


class IntegratedEnvironmentEngine:
    def __init__(self, config: IntegratedEnvironmentConfig,
                 forcing: M5ASpatialForcing):
        self.config = config
        self.forcing = forcing
        self.lat_edges, self.latitudes, self.longitudes = self._grid()
        lat, lon = np.meshgrid(self.latitudes, self.longitudes, indexing="ij")
        self.lat_grid = lat.reshape(-1)
        self.lon_grid = lon.reshape(-1)
        self.cell_count = self.lat_grid.size
        self.cell_area_m2 = 4.0 * math.pi * config.planet_radius_m**2 / self.cell_count
        self.normals_body = np.stack([
            np.cos(self.lat_grid) * np.cos(self.lon_grid),
            np.cos(self.lat_grid) * np.sin(self.lon_grid),
            np.sin(self.lat_grid),
        ], axis=1)
        self.axis = np.asarray(config.rotation_axis, dtype=float)
        reference = (
            np.asarray([1.0, 0.0, 0.0]) if abs(self.axis[0]) < 0.9
            else np.asarray([0.0, 1.0, 0.0])
        )
        self.e0 = reference - self.axis * float(reference @ self.axis)
        self.e0 /= np.linalg.norm(self.e0)
        self.e1 = np.cross(self.axis, self.e0)
        self.material_mass = {
            component.material_id: (
                config.surface_material_column_mass_kg_m2 * component.mass_fraction
            )
            for component in config.material_profile.components
        }
        species = config.atmosphere.initial_species_column_mass()
        self.dry_species = {
            name: np.full(self.cell_count, mass)
            for name, mass in species.items() if name != "water_vapor"
        }
        self.initial_vapor = species["water_vapor"]
        self.dry_column = sum(array for array in self.dry_species.values())
        self.base_surface_capacity = sum(
            self.material_mass[name] * MATERIAL_PROPERTIES[name][0]
            for name in MATERIAL_PROPERTIES
        ) + self.material_mass["water_ice"] * CP_ICE
        self.rock_capacity = (
            config.shallow_rock_column_mass_kg_m2
            * config.shallow_rock_specific_heat_j_kg_k
        )
        self.ocean_capacity = config.ocean_mixed_layer_column_mass_kg_m2 * CP_WATER
        self.air_capacity = config.atmosphere.column_mass_kg_m2 * CP_AIR
        self.initial_water_per_area = (
            self.initial_vapor + config.initial_surface_liquid_water_kg_m2
            + config.initial_surface_ice_kg_m2 + self.material_mass["water_ice"]
            + config.ocean_mixed_layer_column_mass_kg_m2
        )
        self.initial_dry_per_area = float(self.dry_column[0])
        self.material_totals = {
            name: mass * self.cell_area_m2 * self.cell_count
            for name, mass in self.material_mass.items() if name != "water_ice"
        }

    def _grid(self):
        sine_edges = np.linspace(-1.0, 1.0, self.config.latitude_count + 1)
        edges = np.arcsin(sine_edges)
        latitudes = np.arcsin((sine_edges[:-1] + sine_edges[1:]) / 2.0)
        longitudes = -math.pi + (np.arange(self.config.longitude_count) + 0.5) * (
            2.0 * math.pi / self.config.longitude_count
        )
        return edges, latitudes, longitudes

    def initialize(self, start_t_s: float | None = None) -> IntegratedState:
        t_s = self.forcing.coverage_s[0] if start_t_s is None else start_t_s
        if not self.forcing.coverage_s[0] <= t_s <= self.forcing.coverage_s[1]:
            raise ConfigError("environment initial time outside forcing coverage")
        count = self.cell_count
        config = self.config
        ocean_temperature = (
            config.initial_surface_temperature_k if config.initial_ocean_temperature_k is None
            else config.initial_ocean_temperature_k
        )
        state = IntegratedState(
            t_s=t_s, step_index=0, output_index=0,
            surface_temperature_k=np.full(count, config.initial_surface_temperature_k),
            rock_temperature_k=np.full(count, config.initial_rock_temperature_k),
            ocean_temperature_k=np.full(count, ocean_temperature),
            air_temperature_k=np.full(count, config.initial_air_temperature_k),
            vapor_kg_m2=np.full(count, self.initial_vapor),
            supercritical_kg_m2=np.zeros(count),
            cloud_kg_m2=np.zeros(count),
            liquid_kg_m2=np.full(count, config.initial_surface_liquid_water_kg_m2
                                 + config.ocean_mixed_layer_column_mass_kg_m2),
            ice_kg_m2=np.full(count, config.initial_surface_ice_kg_m2
                              + self.material_mass["water_ice"]),
            runoff_kg_m2=np.zeros(count),
            east_wind_m_s=np.zeros(count), north_wind_m_s=np.zeros(count),
            event_active=np.zeros((len(EVENT_TYPES), count), dtype=bool),
            event_enter_s=np.zeros((len(EVENT_TYPES), count)),
            event_exit_s=np.zeros((len(EVENT_TYPES), count)),
            event_cooldown_s=np.zeros((len(EVENT_TYPES), count)),
            active_event_index=np.full((len(EVENT_TYPES), count), -1, dtype=int),
        )
        self._initialize_water_phase(state)
        state.initial_total_energy_j = self.total_energy(state)
        return state

    def _initialize_water_phase(self, state: IntegratedState) -> None:
        """Put initial water in a pressure/temperature-consistent phase regime."""
        total = (
            state.vapor_kg_m2 + state.cloud_kg_m2 + state.liquid_kg_m2
            + state.ice_kg_m2 + state.runoff_kg_m2 + state.supercritical_kg_m2
        )
        state.cloud_kg_m2[:] = 0.0
        state.runoff_kg_m2[:] = 0.0
        hot = state.surface_temperature_k >= WATER_CRITICAL_T_K
        supercritical = hot & (
            total * self.config.surface_gravity_m_s2 >= WATER_CRITICAL_P_PA
        )
        vapor_only = hot & ~supercritical
        saturation_column = self.saturation_pressure_pa(
            state.surface_temperature_k
        ) / self.config.surface_gravity_m_s2
        vapor = np.minimum(total, saturation_column)
        condensate = total - vapor
        frozen = state.surface_temperature_k < 273.15
        state.vapor_kg_m2 = np.where(vapor_only, total, np.where(supercritical, 0.0, vapor))
        state.supercritical_kg_m2 = np.where(supercritical, total, 0.0)
        state.ice_kg_m2 = np.where((~hot) & frozen, condensate, 0.0)
        state.liquid_kg_m2 = np.where((~hot) & ~frozen, condensate, 0.0)

    def _body_basis(self, t_s: float) -> np.ndarray:
        angle = self.config.longitude_phase_rad + self.config.rotation_rate_rad_s * t_s
        x_axis = math.cos(angle) * self.e0 + math.sin(angle) * self.e1
        y_axis = -math.sin(angle) * self.e0 + math.cos(angle) * self.e1
        return np.stack((x_axis, y_axis, self.axis), axis=1)

    def incident_stellar_flux(self, t_s: float) -> tuple[np.ndarray, tuple[str, ...]]:
        sample = self.forcing.sample(t_s)
        normals_inertial = self.normals_body @ self._body_basis(t_s).T
        cosine = np.maximum(0.0, normals_inertial @ sample.star_direction_inertial.T)
        flags = () if sample.interpolation == "EXACT_SAMPLE" else (
            "UNRESOLVED_ORBITAL_FORCING_BETWEEN_SAMPLES",
        )
        return cosine @ sample.stellar_flux_w_m2, flags

    def _reshape(self, values: np.ndarray) -> np.ndarray:
        return values.reshape(self.config.latitude_count, self.config.longitude_count)

    def _laplacian(self, values: np.ndarray) -> np.ndarray:
        grid = self._reshape(values)
        result = np.roll(grid, 1, axis=1) + np.roll(grid, -1, axis=1) - 2.0 * grid
        result[:-1] += grid[1:] - grid[:-1]
        result[1:] += grid[:-1] - grid[1:]
        return result.reshape(-1)

    @staticmethod
    def saturation_pressure_pa(temperature_k: np.ndarray) -> np.ndarray:
        # Murphy-Koop-like two-branch game reference; bounded before exponentiation.
        liquid = 611.2 * np.exp(np.clip(
            17.67 * (temperature_k - 273.15) / (temperature_k - 29.65), -80.0, 80.0
        ))
        ice = 611.15 * np.exp(np.clip(
            22.452 * (temperature_k - 273.15) / (temperature_k - 0.55), -80.0, 80.0
        ))
        return np.where(temperature_k >= 273.15, liquid, ice)

    def _material_phase(self, temperature: np.ndarray, pressure: np.ndarray):
        total_liquid = np.zeros(self.cell_count)
        magma = np.zeros(self.cell_count)
        effective_capacity = np.full(self.cell_count, self.base_surface_capacity)
        latent_energy = np.zeros(self.cell_count)
        total_mass = sum(self.material_mass.values())
        for name, mass in self.material_mass.items():
            if name == "water_ice":
                continue
            _, solidus, liquidus, slope, latent = MATERIAL_PROPERTIES[name]
            solidus_p = np.maximum(1.0, solidus + slope * (pressure - 101325.0))
            liquidus_p = np.maximum(solidus_p + 1.0, liquidus + slope * (pressure - 101325.0))
            fraction = np.clip((temperature - solidus_p) / (liquidus_p - solidus_p), 0.0, 1.0)
            active = (temperature > solidus_p) & (temperature < liquidus_p)
            effective_capacity += active * mass * latent / (liquidus_p - solidus_p)
            latent_energy += mass * latent * fraction
            total_liquid += mass * fraction
            if name in {"basaltic_silicate", "granitic_silicate"}:
                magma += mass * fraction
        return total_liquid / total_mass, magma / total_mass, effective_capacity, latent_energy

    def _gas_diagnostics(self, state: IntegratedState):
        total_column = (
            self.dry_column + state.vapor_kg_m2 + state.cloud_kg_m2
            + state.supercritical_kg_m2
        )
        pressure = self.config.surface_gravity_m_s2 * total_column
        moles = {
            name: mass / GAS_MOLAR_MASS_KG_MOL[name]
            for name, mass in self.dry_species.items()
        }
        moles["water_vapor"] = state.vapor_kg_m2 / GAS_MOLAR_MASS_KG_MOL["water_vapor"]
        total_moles = sum(moles.values())
        mean_molar = np.divide(
            total_column - state.cloud_kg_m2, total_moles,
            out=np.full(self.cell_count, np.nan), where=total_moles > 0.0,
        )
        gas_pressure = self.config.surface_gravity_m_s2 * (
            self.dry_column + state.vapor_kg_m2
        )
        density = np.divide(
            gas_pressure * mean_molar, GAS_R * state.air_temperature_k,
            out=np.zeros(self.cell_count), where=np.isfinite(mean_molar),
        )
        vapor_pressure = self.config.surface_gravity_m_s2 * state.vapor_kg_m2
        saturation = self.saturation_pressure_pa(state.air_temperature_k)
        relative_humidity = np.divide(
            vapor_pressure, saturation, out=np.zeros_like(vapor_pressure),
            where=saturation > 0.0,
        )
        specific_humidity = np.divide(
            state.vapor_kg_m2, total_column,
            out=np.zeros_like(total_column), where=total_column > 0.0,
        )
        return pressure, density, mean_molar, relative_humidity, specific_humidity, moles

    def _greenhouse_tau(self, pressure: np.ndarray, state: IntegratedState) -> np.ndarray:
        earth_pressure = pressure / 101325.0
        co2 = self.dry_species["carbon_dioxide"]
        methane = self.dry_species["methane"]
        dry = np.maximum(self.dry_column, 1.0e-30)
        tau = (
            0.15 * np.sqrt(np.maximum(0.0, earth_pressure))
            + 1.5 * np.sqrt(np.maximum(0.0, co2 / dry))
            + 0.8 * np.sqrt(np.maximum(0.0, methane / dry))
            + 0.35 * np.sqrt(np.maximum(0.0, state.vapor_kg_m2 / 10.0))
        )
        return np.clip(tau, 0.0, 20.0)

    def total_energy(self, state: IntegratedState) -> float:
        pressure, _, _, _, _, _ = self._gas_diagnostics(state)
        _, _, _, rock_latent = self._material_phase(state.surface_temperature_k, pressure)
        sensible = (
            self.base_surface_capacity * state.surface_temperature_k
            + self.rock_capacity * state.rock_temperature_k
            + self.ocean_capacity * state.ocean_temperature_k
            + self.air_capacity * state.air_temperature_k
        )
        water_latent = (
            (state.liquid_kg_m2 + state.cloud_kg_m2 + state.runoff_kg_m2)
            * LATENT_FUSION_WATER
            + state.vapor_kg_m2 * (
                LATENT_FUSION_WATER + LATENT_VAPORIZATION_WATER
            )
            + state.supercritical_kg_m2 * (
                LATENT_FUSION_WATER + LATENT_VAPORIZATION_WATER
            )
        )
        return float(np.sum(sensible + rock_latent + water_latent) * self.cell_area_m2)

    def _wind_targets(self, pressure: np.ndarray, temperature: np.ndarray,
                      density: np.ndarray):
        virtual = pressure * temperature / max(float(np.mean(temperature)), 1.0)
        grid = self._reshape(virtual)
        dlon = 2.0 * math.pi / self.config.longitude_count
        east_distance = self.config.planet_radius_m * np.maximum(
            np.cos(self.latitudes), 0.05
        )[:, None] * (2.0 * dlon)
        grad_east = (np.roll(grid, -1, axis=1) - np.roll(grid, 1, axis=1)) / east_distance
        grad_north = np.zeros_like(grid)
        latitude_distance = self.config.planet_radius_m * np.diff(self.latitudes)
        grad_north[1:-1] = (grid[2:] - grid[:-2]) / (
            self.config.planet_radius_m
            * (self.latitudes[2:, None] - self.latitudes[:-2, None])
        )
        grad_north[0] = (grid[1] - grid[0]) / latitude_distance[0]
        grad_north[-1] = (grid[-1] - grid[-2]) / latitude_distance[-1]
        rho = np.maximum(self._reshape(density), 1.0e-8)
        gx = grad_east / rho
        gy = grad_north / rho
        f = 2.0 * self.config.rotation_rate_rad_s * np.sin(self.latitudes)[:, None]
        drag = 1.0 / self.config.surface_drag_s
        denominator = drag * drag + f * f
        target_east = (-drag * gx - f * gy) / denominator
        target_north = (f * gx - drag * gy) / denominator
        return target_east.reshape(-1), target_north.reshape(-1)

    def _event_conditions(self, state: IntegratedState, rh: np.ndarray,
                          cloud_fraction: np.ndarray, precipitation: np.ndarray):
        speed = np.hypot(state.east_wind_m_s, state.north_wind_m_s)
        enter = np.stack([
            (rh >= 0.97) & (speed < 5.0),
            speed >= 20.0,
            state.air_temperature_k >= 330.0,
            state.air_temperature_k <= 250.0,
            precipitation >= 2.0e-4,
            (cloud_fraction >= 0.8) & (precipitation >= 1.0e-4)
            & ((state.surface_temperature_k - state.air_temperature_k) >= 8.0),
        ])
        remain = np.stack([
            (rh >= 0.92) & (speed <= 7.0),
            speed >= 15.0,
            state.air_temperature_k >= 325.0,
            state.air_temperature_k <= 255.0,
            precipitation >= 1.0e-4,
            (cloud_fraction >= 0.65) & (precipitation >= 5.0e-5)
            & ((state.surface_temperature_k - state.air_temperature_k) >= 5.0),
        ])
        return enter, remain

    def _update_events(self, state: IntegratedState, dt: float, rh: np.ndarray,
                       cloud_fraction: np.ndarray, precipitation: np.ndarray) -> None:
        enter, remain = self._event_conditions(state, rh, cloud_fraction, precipitation)
        state.event_cooldown_s = np.maximum(0.0, state.event_cooldown_s - dt)
        for event_index, event_type in enumerate(EVENT_TYPES):
            active = state.event_active[event_index]
            can_enter = (~active) & (state.event_cooldown_s[event_index] <= 0.0)
            state.event_enter_s[event_index] = np.where(
                can_enter & enter[event_index], state.event_enter_s[event_index] + dt, 0.0
            )
            starts = can_enter & (
                state.event_enter_s[event_index] >= EVENT_ENTER_S[event_index]
            )
            for cell_id in np.flatnonzero(starts):
                record = {
                    "event_id": f"env-{len(state.events):08d}",
                    "cell_id": int(cell_id), "event_type": event_type,
                    "start_t_s": state.t_s + dt, "end_t_s": None,
                    "status": "ACTIVE",
                    "confidence": (
                        "CANDIDATE_UNCERTAIN" if event_type == "THUNDERSTORM_CANDIDATE"
                        else "PARAMETERIZED"
                    ),
                    "message": (
                        "CANDIDATE_UNCERTAIN_NO_VERTICAL_CONVECTION"
                        if event_type == "THUNDERSTORM_CANDIDATE"
                        else "PARAMETERIZED_THRESHOLD_WITH_HYSTERESIS"
                    ),
                }
                state.events.append(record)
                state.active_event_index[event_index, cell_id] = len(state.events) - 1
            state.event_active[event_index, starts] = True
            state.event_enter_s[event_index, starts] = 0.0
            active = state.event_active[event_index]
            state.event_exit_s[event_index] = np.where(
                active & ~remain[event_index], state.event_exit_s[event_index] + dt, 0.0
            )
            ends = active & (state.event_exit_s[event_index] >= EVENT_EXIT_S[event_index])
            for cell_id in np.flatnonzero(ends):
                record_index = state.active_event_index[event_index, cell_id]
                if record_index >= 0:
                    state.events[record_index] = {
                        **state.events[record_index], "end_t_s": state.t_s + dt,
                        "status": "ENDED",
                    }
                state.active_event_index[event_index, cell_id] = -1
            state.event_active[event_index, ends] = False
            state.event_exit_s[event_index, ends] = 0.0
            state.event_cooldown_s[event_index, ends] = EVENT_COOLDOWN_S[event_index]

    def advance(self, state: IntegratedState, target_t_s: float) -> None:
        if target_t_s <= state.t_s or target_t_s > self.forcing.coverage_s[1]:
            raise ConfigError("invalid integrated environment advance target")
        while state.t_s < target_t_s - 1.0e-9:
            dt = min(self.config.max_internal_step_s, target_t_s - state.t_s)
            self._step(state, dt)

    def _step(self, state: IntegratedState, dt: float) -> None:
        config = self.config
        incident, _ = self.incident_stellar_flux(state.t_s + 0.5 * dt)
        absorbed = (1.0 - config.bond_albedo) * incident
        pressure, density, mean_molar, rh, _, _ = self._gas_diagnostics(state)
        tau = self._greenhouse_tau(pressure, state)
        outgoing = (
            config.emissivity * STEFAN_BOLTZMANN_W_M2_K4
            * state.surface_temperature_k**4 / (1.0 + 0.75 * tau)
        )
        external = absorbed + config.internal_heat_flux_w_m2 - outgoing
        to_rock = config.surface_rock_exchange_w_m2_k * (
            state.surface_temperature_k - state.rock_temperature_k
        )
        to_ocean = config.surface_ocean_exchange_w_m2_k * (
            state.surface_temperature_k - state.ocean_temperature_k
        )
        to_air = config.surface_air_exchange_w_m2_k * (
            state.surface_temperature_k - state.air_temperature_k
        ) if config.atmosphere.column_mass_kg_m2 > 0.0 else np.zeros(self.cell_count)
        surface_horizontal = config.horizontal_surface_exchange_w_m2_k * self._laplacian(
            state.surface_temperature_k
        )
        air_horizontal = config.horizontal_air_exchange_w_m2_k * self._laplacian(
            state.air_temperature_k
        )
        saturation = self.saturation_pressure_pa(state.air_temperature_k)
        vapor_pressure = config.surface_gravity_m_s2 * state.vapor_kg_m2
        deficit = np.maximum(0.0, saturation - vapor_pressure)
        potential_evap = config.evaporation_coefficient_kg_m2_s_pa * deficit
        liquid_evap = np.minimum(potential_evap, state.liquid_kg_m2 / dt)
        remaining = np.maximum(0.0, potential_evap - liquid_evap)
        ice_sublimation = np.minimum(0.5 * remaining, state.ice_kg_m2 / dt)
        latent_demand = (
            liquid_evap * LATENT_VAPORIZATION_WATER
            + ice_sublimation * (LATENT_VAPORIZATION_WATER + LATENT_FUSION_WATER)
        )
        available = np.maximum(0.0, absorbed + config.internal_heat_flux_w_m2)
        scale = np.minimum(1.0, np.divide(
            available, latent_demand, out=np.ones_like(available), where=latent_demand > 0.0
        ))
        liquid_evap *= scale
        ice_sublimation *= scale
        supersaturated_mass = np.maximum(
            0.0, (vapor_pressure - saturation) / config.surface_gravity_m_s2
        )
        condensation = np.minimum(state.vapor_kg_m2 / dt, supersaturated_mass / dt)
        precipitation = np.maximum(
            0.0, state.cloud_kg_m2 - config.cloud_threshold_kg_m2
        ) / config.precipitation_timescale_s
        precipitation = np.minimum(precipitation, state.cloud_kg_m2 / dt)
        snow = precipitation * (state.air_temperature_k < 273.15)
        rain = precipitation - snow
        phase_flux = 50.0 * (state.surface_temperature_k - 273.15)
        melt = np.minimum(np.maximum(0.0, phase_flux) / LATENT_FUSION_WATER,
                          state.ice_kg_m2 / dt)
        freeze = np.minimum(np.maximum(0.0, -phase_flux) / LATENT_FUSION_WATER,
                            state.liquid_kg_m2 / dt)
        vapor_delta = (liquid_evap + ice_sublimation - condensation) * dt
        cloud_delta = (condensation - precipitation) * dt
        liquid_delta = (-liquid_evap + rain + melt - freeze) * dt
        ice_delta = (-ice_sublimation + snow - melt + freeze) * dt
        state.vapor_kg_m2 += vapor_delta
        state.cloud_kg_m2 += cloud_delta
        state.liquid_kg_m2 += liquid_delta
        state.ice_kg_m2 += ice_delta
        excess = np.maximum(0.0, state.liquid_kg_m2 - 1.0e4)
        state.liquid_kg_m2 -= excess
        state.runoff_kg_m2 += excess
        moisture_diffusion = config.moisture_exchange_s_inv * self._laplacian(
            state.vapor_kg_m2
        )
        state.vapor_kg_m2 += dt * moisture_diffusion
        if np.any(state.vapor_kg_m2 < -1.0e-12):
            raise ConfigError("moisture transport produced negative inventory")
        state.vapor_kg_m2 = np.maximum(state.vapor_kg_m2, 0.0)
        phase_energy_before = (
            (state.liquid_kg_m2 + state.cloud_kg_m2 + state.runoff_kg_m2)
            * LATENT_FUSION_WATER
            + (state.vapor_kg_m2 + state.supercritical_kg_m2)
            * (LATENT_FUSION_WATER + LATENT_VAPORIZATION_WATER)
        )
        critical_hot = state.surface_temperature_k >= WATER_CRITICAL_T_K
        if np.any(critical_hot) or np.any(state.supercritical_kg_m2 > 0.0):
            total_water = (
                state.vapor_kg_m2 + state.cloud_kg_m2 + state.liquid_kg_m2
                + state.ice_kg_m2 + state.runoff_kg_m2
                + state.supercritical_kg_m2
            )
            supercritical = critical_hot & (
                total_water * config.surface_gravity_m_s2 >= WATER_CRITICAL_P_PA
            )
            vapor_only = critical_hot & ~supercritical
            transitioning_to_normal = (~critical_hot) & (state.supercritical_kg_m2 > 0.0)
            saturation_column = self.saturation_pressure_pa(
                state.surface_temperature_k
            ) / config.surface_gravity_m_s2
            normal_vapor = np.minimum(total_water, saturation_column)
            normal_condensate = total_water - normal_vapor
            state.supercritical_kg_m2[supercritical] = total_water[supercritical]
            for inventory in (
                state.vapor_kg_m2, state.cloud_kg_m2, state.liquid_kg_m2,
                state.ice_kg_m2, state.runoff_kg_m2,
            ):
                inventory[supercritical] = 0.0
            state.vapor_kg_m2[vapor_only] = total_water[vapor_only]
            for inventory in (
                state.cloud_kg_m2, state.liquid_kg_m2, state.ice_kg_m2,
                state.runoff_kg_m2, state.supercritical_kg_m2,
            ):
                inventory[vapor_only] = 0.0
            state.vapor_kg_m2[transitioning_to_normal] = normal_vapor[
                transitioning_to_normal
            ]
            cold = transitioning_to_normal & (state.surface_temperature_k < 273.15)
            warm = transitioning_to_normal & ~cold
            state.ice_kg_m2[cold] = normal_condensate[cold]
            state.liquid_kg_m2[warm] = normal_condensate[warm]
            state.supercritical_kg_m2[transitioning_to_normal] = 0.0
        phase_energy_after = (
            (state.liquid_kg_m2 + state.cloud_kg_m2 + state.runoff_kg_m2)
            * LATENT_FUSION_WATER
            + (state.vapor_kg_m2 + state.supercritical_kg_m2)
            * (LATENT_FUSION_WATER + LATENT_VAPORIZATION_WATER)
        )
        critical_phase_energy_flux = (phase_energy_after - phase_energy_before) / dt
        latent_surface = (
            -liquid_evap * LATENT_VAPORIZATION_WATER
            -ice_sublimation * (LATENT_VAPORIZATION_WATER + LATENT_FUSION_WATER)
            -melt * LATENT_FUSION_WATER + freeze * LATENT_FUSION_WATER
            -critical_phase_energy_flux
        )
        latent_air = (
            condensation * LATENT_VAPORIZATION_WATER
            + snow * LATENT_FUSION_WATER
        )
        _, _, effective_surface_capacity, _ = self._material_phase(
            state.surface_temperature_k, pressure
        )
        state.surface_temperature_k += dt * (
            external - to_rock - to_ocean - to_air + surface_horizontal + latent_surface
        ) / effective_surface_capacity
        state.rock_temperature_k += dt * to_rock / self.rock_capacity
        if self.ocean_capacity > 0.0:
            state.ocean_temperature_k += dt * to_ocean / self.ocean_capacity
        if self.air_capacity > 0.0:
            state.air_temperature_k += dt * (
                to_air + air_horizontal + latent_air
            ) / self.air_capacity
        else:
            state.air_temperature_k[:] = state.surface_temperature_k
        pressure, density, mean_molar, rh, _, _ = self._gas_diagnostics(state)
        target_east, target_north = self._wind_targets(
            pressure, state.air_temperature_k, density
        ) if np.any(pressure > 0.0) else (np.zeros(self.cell_count), np.zeros(self.cell_count))
        wind_fraction = min(1.0, dt / config.wind_relaxation_s)
        state.east_wind_m_s += wind_fraction * (target_east - state.east_wind_m_s)
        state.north_wind_m_s += wind_fraction * (target_north - state.north_wind_m_s)
        sound_speed = np.sqrt(np.maximum(
            1.0, 1.4 * GAS_R * state.air_temperature_k
            / np.where(np.isfinite(mean_molar), mean_molar, 0.029)
        ))
        limit = 0.3 * sound_speed
        speed = np.hypot(state.east_wind_m_s, state.north_wind_m_s)
        limited = speed > limit
        if np.any(limited):
            factor = np.divide(limit, speed, out=np.ones_like(speed), where=speed > 0.0)
            state.east_wind_m_s[limited] *= factor[limited]
            state.north_wind_m_s[limited] *= factor[limited]
            state.wind_limit_hits += int(np.count_nonzero(limited))
        cloud_fraction = 1.0 - np.exp(-state.cloud_kg_m2 / config.cloud_threshold_kg_m2)
        self._update_events(state, dt, rh, cloud_fraction, precipitation)
        state.integrated_external_energy_j += float(np.sum(external) * self.cell_area_m2 * dt)
        state.t_s += dt
        state.step_index += 1
        arrays = (
            state.surface_temperature_k, state.rock_temperature_k,
            state.ocean_temperature_k, state.air_temperature_k,
            state.vapor_kg_m2, state.cloud_kg_m2, state.liquid_kg_m2,
            state.supercritical_kg_m2, state.ice_kg_m2, state.runoff_kg_m2,
        )
        if any(np.any(~np.isfinite(array)) for array in arrays):
            raise ConfigError("integrated environment produced non-finite state")
        if any(np.any(array < -1.0e-10) for array in arrays[4:]):
            raise ConfigError("integrated environment produced negative inventory")
        if any(np.any(array <= 0.0) for array in arrays[:4]):
            raise ConfigError("integrated environment produced non-positive temperature")
        energy = self.total_energy(state)
        residual = energy - state.initial_total_energy_j - state.integrated_external_energy_j
        scale_energy = max(
            abs(state.initial_total_energy_j), abs(state.integrated_external_energy_j), 1.0
        )
        state.maximum_scaled_energy_residual = max(
            state.maximum_scaled_energy_residual, abs(residual) / scale_energy
        )

    def snapshot(self, state: IntegratedState) -> IntegratedSnapshot:
        pressure, density, mean_molar, rh, specific_humidity, moles = (
            self._gas_diagnostics(state)
        )
        total_moles = sum(moles.values())
        mole_fractions = {
            name: np.divide(value, total_moles, out=np.zeros(self.cell_count),
                            where=total_moles > 0.0)
            for name, value in moles.items()
        }
        material_liquid, magma, _, _ = self._material_phase(
            state.surface_temperature_k, pressure
        )
        cloud_fraction = 1.0 - np.exp(
            -state.cloud_kg_m2 / self.config.cloud_threshold_kg_m2
        )
        precipitation = np.maximum(
            0.0, state.cloud_kg_m2 - self.config.cloud_threshold_kg_m2
        ) / self.config.precipitation_timescale_s
        active_by_cell = [
            tuple(EVENT_TYPES[event_index] for event_index in range(len(EVENT_TYPES))
                  if state.event_active[event_index, cell_id])
            for cell_id in range(self.cell_count)
        ]
        cells = []
        for cell_id in range(self.cell_count):
            gasless = total_moles[cell_id] <= 0.0
            flags = []
            if self.forcing.max_gap_s > self.config.max_internal_step_s:
                flags.append("UNRESOLVED_ORBITAL_FORCING_BETWEEN_SAMPLES")
            if not gasless and (
                pressure[cell_id] < 0.01 or pressure[cell_id] > 1.0e7
                or state.air_temperature_k[cell_id] < 100.0
                or state.air_temperature_k[cell_id] > 1500.0
            ):
                flags.append("UNCERTAIN_IDEAL_GAS_RANGE")
            if state.surface_temperature_k[cell_id] > 2500.0:
                flags.append("SURFACE_PHASE_PROPERTIES_UNAVAILABLE_ABOVE_2500_K")
            if state.supercritical_kg_m2[cell_id] > 0.0:
                flags.append("SUPERCRITICAL_WATER_UNCERTAIN")
            water_inventories = {
                "ice": state.ice_kg_m2[cell_id],
                "liquid": (
                    state.liquid_kg_m2[cell_id] + state.runoff_kg_m2[cell_id]
                ),
                "vapor": state.vapor_kg_m2[cell_id],
                "supercritical": state.supercritical_kg_m2[cell_id],
            }
            water_inventories["vapor"] += state.cloud_kg_m2[cell_id]
            water_total = sum(water_inventories.values())
            if water_total <= 0.0:
                water_regime = "ABSENT"
            elif water_inventories["supercritical"] > 0.5 * water_total:
                water_regime = "SUPERCRITICAL_UNCERTAIN"
            else:
                dominant = max(water_inventories, key=water_inventories.get)
                fraction = water_inventories[dominant] / water_total
                water_regime = (
                    {"ice": "ICE_DOMINANT", "liquid": "LIQUID_DOMINANT",
                     "vapor": "VAPOR_DOMINANT"}[dominant]
                    if fraction >= 0.8 else "MIXED"
                )
            if precipitation[cell_id] > 0.0:
                phase = "SNOW" if state.air_temperature_k[cell_id] < 273.15 else "RAIN"
                weather = phase
            elif cloud_fraction[cell_id] >= 0.7:
                phase = "NONE"
                weather = "OVERCAST"
            elif cloud_fraction[cell_id] >= 0.2:
                phase = "NONE"
                weather = "PARTLY_CLOUDY"
            else:
                phase = "NONE"
                weather = "CLEAR"
            gas = {name: float(values[cell_id]) for name, values in mole_fractions.items()}
            gas_pressure = self.config.surface_gravity_m_s2 * (
                self.dry_column[cell_id] + state.vapor_kg_m2[cell_id]
            )
            partial_pressures = {
                name: fraction * gas_pressure for name, fraction in gas.items()
            }
            air_quality = "UNAVAILABLE" if gasless else (
                "UNCERTAIN" if "UNCERTAIN_IDEAL_GAS_RANGE" in flags else "PARAMETERIZED"
            )
            pressure_quality = (
                "UNCERTAIN" if state.supercritical_kg_m2[cell_id] > 0.0
                else air_quality
            )
            cells.append(IntegratedCellState(
                cell_id=cell_id,
                latitude_deg=math.degrees(float(self.lat_grid[cell_id])),
                longitude_deg=math.degrees(float(self.lon_grid[cell_id])),
                surface_temperature_k=float(state.surface_temperature_k[cell_id]),
                rock_temperature_k=float(state.rock_temperature_k[cell_id]),
                ocean_temperature_k=(
                    None if self.ocean_capacity == 0.0
                    else float(state.ocean_temperature_k[cell_id])
                ),
                air_temperature_k=(None if gasless else float(state.air_temperature_k[cell_id])),
                surface_pressure_pa=float(pressure[cell_id]),
                air_density_kg_m3=(None if gasless else float(density[cell_id])),
                mean_molar_mass_kg_mol=(
                    None if gasless else float(mean_molar[cell_id])
                ),
                gas_mole_fractions=gas,
                gas_partial_pressures_pa=partial_pressures,
                specific_humidity_kg_kg=(
                    None if gasless else float(specific_humidity[cell_id])
                ),
                relative_humidity=(None if gasless else float(rh[cell_id])),
                eastward_wind_m_s=(None if gasless else float(state.east_wind_m_s[cell_id])),
                northward_wind_m_s=(None if gasless else float(state.north_wind_m_s[cell_id])),
                cloud_fraction=float(cloud_fraction[cell_id]),
                precipitation_rate_kg_m2_s=float(precipitation[cell_id]),
                precipitation_phase=phase,
                liquid_water_kg_m2=float(state.liquid_kg_m2[cell_id]),
                ice_kg_m2=float(state.ice_kg_m2[cell_id]),
                water_vapor_kg_m2=float(state.vapor_kg_m2[cell_id]),
                supercritical_water_kg_m2=float(
                    state.supercritical_kg_m2[cell_id]
                ),
                cloud_water_kg_m2=float(state.cloud_kg_m2[cell_id]),
                runoff_kg_m2=float(state.runoff_kg_m2[cell_id]),
                surface_material_liquid_fraction=float(material_liquid[cell_id]),
                magma_fraction=float(magma[cell_id]),
                water_phase_regime=water_regime,
                weather_state=weather,
                active_events=active_by_cell[cell_id],
                quality={
                    "surface_temperature_k": "COMPUTED",
                    "air_temperature_k": air_quality,
                    "surface_pressure_pa": pressure_quality,
                    "humidity": air_quality,
                    "wind": air_quality,
                    "precipitation": "PARAMETERIZED" if not gasless else "UNAVAILABLE",
                    "surface_phase": (
                        "UNAVAILABLE" if state.surface_temperature_k[cell_id] > 2500.0
                        else "PARAMETERIZED"
                    ),
                },
                uncertainty_flags=tuple(flags),
            ))
        state.output_index += 1
        area = self.cell_area_m2
        return IntegratedSnapshot(
            t_s=state.t_s, time_index=state.output_index - 1,
            cells=tuple(cells), total_energy_j=self.total_energy(state),
            total_water_kg=float(np.sum(
                state.vapor_kg_m2 + state.cloud_kg_m2 + state.liquid_kg_m2
                + state.supercritical_kg_m2 + state.ice_kg_m2 + state.runoff_kg_m2
            ) * area),
            total_dry_atmosphere_kg=float(np.sum(self.dry_column) * area),
        )

    def diagnostics(self, state: IntegratedState) -> IntegratedDiagnostics:
        area = self.cell_area_m2
        water = float(np.sum(
            state.vapor_kg_m2 + state.cloud_kg_m2 + state.liquid_kg_m2
            + state.supercritical_kg_m2 + state.ice_kg_m2 + state.runoff_kg_m2
        ) * area)
        initial_water = self.initial_water_per_area * area * self.cell_count
        dry = float(np.sum(self.dry_column) * area)
        initial_dry = self.initial_dry_per_area * area * self.cell_count
        return IntegratedDiagnostics(
            maximum_scaled_energy_residual=state.maximum_scaled_energy_residual,
            maximum_water_mass_relative_residual=(
                0.0 if initial_water == 0.0 else abs(water - initial_water) / initial_water
            ),
            maximum_dry_atmosphere_relative_residual=(
                0.0 if initial_dry == 0.0 else abs(dry - initial_dry) / initial_dry
            ),
            maximum_material_mass_relative_residual=0.0,
            wind_stability_limit_hits=state.wind_limit_hits,
            orbital_forcing_max_gap_s=self.forcing.max_gap_s,
            internal_step_count=state.step_index,
        )

    def result(self, snapshots: list[IntegratedSnapshot], state: IntegratedState,
               events: list[dict[str, Any]] | None = None) -> IntegratedEnvironmentResult:
        runtime = {
            "project": __version__, "python": platform.python_version(),
            "numpy": np.__version__,
        }
        event_models = tuple(
            EnvironmentEvent.model_validate(event, strict=True)
            for event in (state.events if events is None else events)
        )
        diagnostics = self.diagnostics(state)
        payload = {
            "schema_version": "1.0.0", "model_id": "integrated_environment_v1",
            "config_sha256": self.config.config_sha256,
            "forcing_source_sha256": self.forcing.source_sha256,
            "runtime_versions": runtime,
            "snapshots": [snapshot.model_dump(mode="json") for snapshot in snapshots],
            "events": [event.model_dump(mode="json") for event in event_models],
            "diagnostics": diagnostics.model_dump(mode="json"),
        }
        return IntegratedEnvironmentResult(
            config_sha256=self.config.config_sha256,
            forcing_source_sha256=self.forcing.source_sha256,
            runtime_versions=runtime, snapshots=tuple(snapshots), events=event_models,
            diagnostics=diagnostics, result_sha256=sha256_json(payload),
        )


def simulate_integrated_environment(
    config: IntegratedEnvironmentConfig, forcing: M5ASpatialForcing,
    output_times_s: list[float] | tuple[float, ...],
) -> IntegratedEnvironmentResult:
    if len(output_times_s) < 2 or any(
        b <= a for a, b in zip(output_times_s, output_times_s[1:], strict=False)
    ):
        raise ConfigError("integrated output times must be strictly increasing")
    if output_times_s[0] < forcing.coverage_s[0] or output_times_s[-1] > forcing.coverage_s[1]:
        raise ConfigError("integrated output times exceed forcing coverage")
    engine = IntegratedEnvironmentEngine(config, forcing)
    state = engine.initialize(output_times_s[0])
    snapshots = [engine.snapshot(state)]
    for target in output_times_s[1:]:
        engine.advance(state, target)
        snapshots.append(engine.snapshot(state))
    return engine.result(snapshots, state)
