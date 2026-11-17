from __future__ import annotations

from pathlib import Path

from threebody.experiments.simulation import load_initial_state
from threebody.physics.longrun.config import load_long_run_settings
from threebody.physics.longrun.runner import (
    LongRunInvocationResult,
    resume_long_run,
    start_long_run,
)


def run_long_simulation_archive(
    initial_path: str | Path,
    config_path: str | Path,
    out_dir: str | Path,
    *,
    run_id: str | None = None,
) -> LongRunInvocationResult:
    initial_state = load_initial_state(initial_path)
    settings, _ = load_long_run_settings(config_path)
    return start_long_run(
        initial_state,
        settings,
        out_dir,
        run_id=run_id or f"{initial_state.world_id}-m5a",
    )


def resume_long_simulation_archive(
    run_dir: str | Path, *, max_wall_seconds: float | None = None
) -> LongRunInvocationResult:
    return resume_long_run(run_dir, max_wall_seconds=max_wall_seconds)
