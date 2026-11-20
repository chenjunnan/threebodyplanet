"""M7.2 authoritative, time-evolving planetary environment model."""

from threebody.physics.environment.forcing import (
    ConstantForcing,
    M5AOrbitForcing,
    StepForcing,
)
from threebody.physics.environment.integrated_archive import (
    IntegratedEnvironmentArchiveReader,
    run_integrated_environment_archive,
)
from threebody.physics.environment.integrated_models import (
    AtmosphereInventoryConfig,
    IntegratedEnvironmentConfig,
    IntegratedEnvironmentResult,
    atmosphere_inventory_from_world_prior,
)
from threebody.physics.environment.integrated_solver import (
    IntegratedEnvironmentEngine,
    simulate_integrated_environment,
)
from threebody.physics.environment.models import (
    DynamicEnvironmentConfig,
    DynamicEnvironmentResult,
    DynamicEnvironmentState,
    ThermalReservoir,
)
from threebody.physics.environment.solver import solve_environment
from threebody.physics.environment.spatial_forcing import M5ASpatialForcing
from threebody.physics.environment.zonal_models import (
    EnvironmentPointQuery,
    EnvironmentPointState,
    PlanetMaterialProfile,
    ZonalDynamicEnvironmentConfig,
    ZonalDynamicEnvironmentResult,
    sample_planet_material_profile,
)
from threebody.physics.environment.zonal_solver import (
    query_zonal_environment,
    solve_zonal_environment,
)

__all__ = [
    "ConstantForcing",
    "AtmosphereInventoryConfig",
    "atmosphere_inventory_from_world_prior",
    "DynamicEnvironmentConfig",
    "DynamicEnvironmentResult",
    "DynamicEnvironmentState",
    "M5AOrbitForcing",
    "M5ASpatialForcing",
    "StepForcing",
    "ThermalReservoir",
    "IntegratedEnvironmentArchiveReader",
    "IntegratedEnvironmentConfig",
    "IntegratedEnvironmentEngine",
    "IntegratedEnvironmentResult",
    "EnvironmentPointQuery",
    "EnvironmentPointState",
    "PlanetMaterialProfile",
    "ZonalDynamicEnvironmentConfig",
    "ZonalDynamicEnvironmentResult",
    "query_zonal_environment",
    "sample_planet_material_profile",
    "solve_environment",
    "solve_zonal_environment",
    "run_integrated_environment_archive",
    "simulate_integrated_environment",
]
