"""Regression: finished legacy sparse M5A orbits can derive clearly qualified M3.

Coarse Hermite temperatures are approximate and MUST expose their lower
scientific confidence; a cadence error must not force re-running IAS15.
"""
from __future__ import annotations

import json
from importlib.resources import files

import pytest
import yaml

from threebody.errors import ConfigError
from threebody.experiments.longrun_environment import derive_long_run_environment
from threebody.physics.longrun.config import load_long_run_settings
from threebody.physics.longrun.models import LongRunSettings
from threebody.physics.longrun.runner import start_long_run
from threebody.web.thermal_science import read_long_environment
from threebody.world.generators import generate_initial_state


@pytest.mark.parametrize("output_days", [5, 30])
def test_legacy_five_or_thirty_day_output_can_derive_c0_with_quality_warning(
    tmp_path, resolved, output_days
):
    config, digest = resolved
    initial = generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )
    settings, _ = load_long_run_settings("configs/m5a_long_run.yaml")
    raw = settings.model_dump(mode="json")
    duration = output_days * 86400.0
    raw["time"] = {
        "start_time_s": 0.0, "end_time_s": duration,
        "output_interval_s": duration,
    }
    raw["segmentation"] = {"simulated_duration_s": duration}
    raw["checkpoint"] = {
        "simulation_interval_s": duration,
        "wall_interval_s": None,
        "retain_count": 3,
    }
    raw["resources"]["max_segments"] = 2
    raw["resources"]["max_samples"] = 3
    run = tmp_path / f"legacy-{output_days}-day"
    result = start_long_run(
        initial, LongRunSettings.model_validate(raw, strict=True),
        run, run_id=run.name,
    )
    assert result.manifest.task.status == "COMPLETED"
    document = yaml.safe_load(
        files("threebody.web.static").joinpath("m3_default.yaml").read_text()
    )
    # C0 shares the exact orbital cadence validation with C1, but is a much
    # faster regression than a 30-day 36-band BDF integration.
    document["thermal"]["mode"] = "c0"
    path = tmp_path / f"thermal-{output_days}.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    derived = tmp_path / "environments" / run.name
    manifest = derive_long_run_environment(run, path, derived)
    assert manifest["sampling_quality"] == "COARSE_UNVERIFIED_TEMPORAL_VARIABILITY"
    assert manifest["source_max_orbit_gap_s"] == duration
    assert "low-confidence" in manifest["sampling_warning"]
    rows = read_long_environment(run, derived)
    assert rows["source_max_orbit_gap_s"] == duration
    assert rows["sampling_quality"] == manifest["sampling_quality"]
    assert rows["sampling_warning"] == manifest["sampling_warning"]
    assert len(rows["rows"]) == 2
    assert all(point["c0_temperature_k"] > 0 for point in rows["rows"])
    assert all(point["c1_temperature_k"] is None for point in rows["rows"])
    assert "source_max_orbit_gap_s" in json.loads(
        (derived / "long_environment_manifest.json").read_text()
    )


    document["input"]["max_orbit_sample_interval_s"] = duration / 2
    disallowed = tmp_path / f"too-strict-{output_days}.yaml"
    disallowed.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ConfigError, match="verified max gap"):
        derive_long_run_environment(
            run, disallowed, tmp_path / f"must-not-exist-{output_days}"
        )
