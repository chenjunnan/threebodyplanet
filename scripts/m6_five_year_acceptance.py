"""M6 real five-year end-to-end IAS15 -> M3/C0/C1 -> M4 acceptance.

No synthetic trajectory data; no GPU time or browser interpolation.
Run: python scripts/m6_five_year_acceptance.py --artifact-root /tmp/m6-accept
A 30-day archival segment is NOT a numerical IAS15 step: each 6-hour
output target is actually integrated by REBOUND.
"""
from __future__ import annotations

import argparse
import json
import math
import resource
import time
from pathlib import Path

from threebody.common.constants import M_EARTH_KG, M_SUN_KG, G
from threebody.common.json import sha256_json, write_json_atomic
from threebody.physics.longrun.models import LongRunSettings
from threebody.physics.longrun.reader import SegmentedTrajectoryReader
from threebody.physics.longrun.runner import start_long_run
from threebody.physics.stellar.main_sequence import main_sequence_properties
from threebody.screening.s0 import validate_s0
from threebody.web.outcome_science import read_long_outcome
from threebody.web.pipeline import derive_run_science
from threebody.web.thermal_science import read_long_environment
from threebody.web.worldsets import YEAR_S, simulation_settings
from threebody.world.models import BodyKind, CelestialBody, WorldInitialState

AU_M = 149597870700.0
FIVE_YEARS_S = 5*YEAR_S
SIX_HOURS_S = 21600.0
RUN_ID = "m6-five-year-acceptance"


def initial_world() -> WorldInitialState:
    """Reproducible wide stellar geometry and a roughly solar-like planet.

    Three physical stars and a planet, not a forced/constant irradiance curve.
    The two distant stars start 200/400 AU from star-1; all stars participate
    in mutual Newtonian gravity throughout the five physical years.
    """
    masses = [M_SUN_KG]*3 + [M_EARTH_KG]
    xs = [-100*AU_M, 100*AU_M, 300*AU_M, -99*AU_M]
    velocities = [0.0, 0.0, 0.0, math.sqrt(G*M_SUN_KG/AU_M)]
    total = sum(masses)
    com_x = sum(m*x for m,x in zip(masses,xs,strict=True))/total
    com_v = sum(m*v for m,v in zip(masses,velocities,strict=True))/total
    radius, luminosity = main_sequence_properties(1.0)
    bodies=[]
    for i, name in enumerate(("star-1","star-2","star-3","planet-1")):
        star=i<3
        bodies.append(CelestialBody(
            id=name,kind=BodyKind.STAR if star else BodyKind.PLANET,
            mass_kg=float(masses[i]),radius_m=float(radius if star else 6.371e6),
            position_m=(float(xs[i]-com_x),0.0,0.0),
            velocity_mps=(0.0,float(velocities[i]-com_v),0.0),
            physical_profile_id="approx_main_sequence_v1" if star else "rocky_density_v1",
            properties=(
                {"luminosity_w":luminosity,
                 "mass_luminosity_model":"piecewise_main_sequence_0p3_8_v1"}
                if star else {"composition_profile_id":"rocky_mean_v1",
                              "mean_density_kg_m3":5514.0}
            ),
        ))
    state=WorldInitialState(
        world_id="candidate-000000",seed=20261117,candidate_index=0,
        generator_id="m6_five_year_acceptance",
        generator_version="1.0.0",
        config_sha256=sha256_json({"fixture":"wide-triple-primary-planet-v1"}),
        bodies=tuple(bodies),
    )
    errors=validate_s0(state)
    if errors:
        raise RuntimeError("M6 acceptance initial state failed S0: "+str(errors))
    return state


def memory_peak_mib() -> float:
    # ru_maxrss is KiB on Linux CI runners.
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024.0


def accept(root: Path, *, duration_s: float=FIVE_YEARS_S,
           interval_s: float=SIX_HOURS_S, repeat_science: bool=True) -> dict:
    root.mkdir(parents=True,exist_ok=True)
    state=initial_world()
    settings=simulation_settings(duration_s,interval_s)
    data=settings.model_dump(mode="json")
    # The acceptance is uninterrupted on one runner; science recovery is
    # separately verified. This disables only wall-clock invocation cutoff.
    data["resources"]["max_wall_seconds_per_invocation"]=None
    settings=LongRunSettings.model_validate(data,strict=True)
    samples_expected=int(math.ceil(duration_s/interval_s))+1
    assert settings.resources.max_samples>=samples_expected
    run=root/RUN_ID
    if run.exists():
        raise RuntimeError("M6 acceptance refuses to overwrite existing physical archive")
    checkpoints=root/"thermal_checkpoints"/RUN_ID
    metrics={}
    start=time.perf_counter()
    integration=start_long_run(state,settings,run,run_id=RUN_ID)
    metrics["ias15_wall_s"]=time.perf_counter()-start
    if integration.manifest.task.status!="COMPLETED":
        raise AssertionError(
            "Five-year IAS15 did not complete; status="+integration.manifest.task.status
        )
    reader=SegmentedTrajectoryReader(run)
    times=[sample.t_s for sample in reader.iter_samples()]
    assert len(times)==samples_expected,(len(times),samples_expected)
    assert times[0]==0.0 and times[-1]==duration_s
    assert max(b-a for a,b in zip(times,times[1:],strict=False))<=interval_s+1e-6
    assert abs(times[-1]-FIVE_YEARS_S)<=1e-6 if duration_s==FIVE_YEARS_S else True

    start=time.perf_counter()
    science=derive_run_science(root,RUN_ID)
    metrics["m3_m4_wall_s"]=time.perf_counter()-start
    assert science["thermal"]=="COMPLETED",science
    assert science["outcome"]=="COMPLETED",science
    envdir=root/"environments"/RUN_ID
    env=read_long_environment(run,envdir,max_points=100)
    assert env["sample_count"]==samples_expected
    assert env["sampling_quality"]=="DAILY_OR_FINER"
    assert env["thermal_energy_residual"]["c0"]<1e-4
    assert env["thermal_energy_residual"]["c1"]<1e-4
    minimum_chunks=2 if samples_expected>128 else 1
    assert env["thermal_processing"]["computed_chunks"]>=minimum_chunks
    assert all(row["c0_temperature_k"]>0 for row in env["rows"])
    assert all(len(row["c1_temperature_k"])==36 for row in env["rows"])
    assert all(row["flux_w_m2"]["total"]>0 for row in env["rows"])

    # Check retained temperature source and validation of M4 science linkage.
    outcome=read_long_outcome(run,root/"outcomes"/RUN_ID,envdir)
    assert outcome["run_id"]==RUN_ID

    if repeat_science:
        from importlib.resources import files

        from threebody.experiments.longrun_environment import derive_long_run_environment

        temp_cfg=root/"m6_thermal_settings.yaml"
        temp_cfg.write_text(
            files("threebody.web.static").joinpath("m3_default.yaml").read_text(),
            encoding="utf-8",
        )
        repeat=root/"m6_resumed_environment"
        start=time.perf_counter()
        repeated=derive_long_run_environment(
            run,temp_cfg,repeat,checkpoint_dir=checkpoints,
        )
        metrics["resumed_m3_wall_s"]=time.perf_counter()-start
        assert repeated["processing"]["computed_chunks"]==0
        assert repeated["processing"]["reused_chunks"]>=minimum_chunks
        assert repeated["sample_count"]==samples_expected
        assert (repeat/"c1_latitude_temperature.jsonl").read_bytes()==(
            envdir/"c1_latitude_temperature.jsonl"
        ).read_bytes()

    metrics["peak_memory_mib"]=memory_peak_mib()
    passed={
        "status":"PASS","source":"REAL_REBOUND_IAS15_NOT_INTERPOLATED",
        "run_id":RUN_ID,"duration_s":duration_s,
        "orbital_output_interval_s":interval_s,
        "verified_physical_samples":samples_expected,
        "scientific_modes":["C0","C1","M4"],
        "source_quality":env["sampling_quality"],
        "normalized_residual_c0":env["thermal_energy_residual"]["c0"],
        "normalized_residual_c1":env["thermal_energy_residual"]["c1"],
        "thermal_recovery_verified":repeat_science,
        "metrics":metrics,
        "caveat":"Finite 5y window only; no general proof of orbital stability/habitability.",
    }
    write_json_atomic(root/"m6_five_year_acceptance.json",passed)
    return passed


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--artifact-root",type=Path,required=True)
    parser.add_argument("--smoke-seconds",type=float,default=None,
                        help="Explicit short CI smoke; never label as five-year acceptance")
    args=parser.parse_args()
    duration=FIVE_YEARS_S if args.smoke_seconds is None else args.smoke_seconds
    interval=SIX_HOURS_S if args.smoke_seconds is None else 10.0
    if duration<60 or (args.smoke_seconds is not None and duration>=FIVE_YEARS_S):
        parser.error("smoke must be >=60 seconds and strictly below 5y")
    result=accept(args.artifact_root,duration_s=duration,interval_s=interval)
    if args.smoke_seconds is not None:
        result["status"]="SMOKE_ONLY_NOT_FIVE_YEAR_ACCEPTANCE"
        write_json_atomic(args.artifact_root/"m6_five_year_acceptance.json",result)
    print(json.dumps(result,indent=2,ensure_ascii=False))


if __name__=="__main__":
    main()
