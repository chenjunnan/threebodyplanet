"""Safe local post-integration M3/M4 pipeline, fully separate from REBOUND.

Only committed completed runs derive M3. Collision runs receive M4 dynamics-only
classification without invented collision aftermath. Derived artifacts are immutable.
"""
from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory

from threebody.errors import ConfigError
from threebody.experiments.longrun_assessment import assess_long_run
from threebody.experiments.longrun_environment import derive_long_run_environment
from threebody.physics.longrun.archive import load_run_manifest
from threebody.web.outcome_science import read_long_outcome
from threebody.web.thermal_science import read_long_environment


def derive_run_science(root: Path, run_id: str) -> dict[str, str]:
    run = root / run_id
    manifest = load_run_manifest(run)
    status = manifest.task.status
    if status not in {"COMPLETED", "COLLISION"}:
        raise ConfigError("science derivation requires a physical terminal archive")
    env_dir = root / "environments" / run_id
    outcome_dir = root / "outcomes" / run_id
    result: dict[str, str] = {
        "thermal": "NOT_COMPUTED",
        "outcome": "NOT_COMPUTED",
    }
    with TemporaryDirectory(prefix="threebody-science-") as temp:
        thermal_config = Path(temp) / "thermal.yaml"
        outcome_config = Path(temp) / "outcome.yaml"
        thermal_config.write_text(
            files("threebody.web.static").joinpath("m3_default.yaml").read_text(),
            encoding="utf-8",
        )
        outcome_config.write_text(
            files("threebody.web.static").joinpath("m4_default.yaml").read_text(),
            encoding="utf-8",
        )
        if status == "COMPLETED":
            if env_dir.exists():
                read_long_environment(run, env_dir)
                result["thermal"] = "VERIFIED_EXISTING"
            else:
                try:
                    derive_long_run_environment(run, thermal_config, env_dir)
                    result["thermal"] = "COMPLETED"
                except (ConfigError, ValueError, ArithmeticError, RuntimeError) as exc:
                    # Solver and input-domain failures do not erase the valid M5A
                    # trajectory. M4 still runs in dynamics-only mode, and its
                    # independent reader must verify the original source chain.
                    result["thermal"] = (
                        f"UNAVAILABLE: {type(exc).__name__}: {exc}"
                    )
        else:
            result["thermal"] = "NOT_APPLICABLE_COLLISION"
        validated_env = env_dir if result["thermal"] in {
            "VERIFIED_EXISTING", "COMPLETED"
        } else None
        if outcome_dir.exists():
            read_long_outcome(run, outcome_dir, validated_env)
            result["outcome"] = "VERIFIED_EXISTING"
        else:
            assess_long_run(
                run, outcome_config, outcome_dir, environment_dir=validated_env
            )
            result["outcome"] = "COMPLETED"
    return result
