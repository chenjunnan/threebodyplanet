from __future__ import annotations

import platform
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from threebody import __version__
from threebody.common.constants import CONSTANT_SET_VERSION
from threebody.common.json import write_json_atomic
from threebody.config.models import ResolvedExperimentConfig
from threebody.screening.engine import screen_candidate
from threebody.screening.models import ScreeningReport
from threebody.screening.rules import RULES
from threebody.world.generators import generate_initial_state


def _summary(reports: list[ScreeningReport]) -> dict[str, Any]:
    decisions = Counter(report.decision.lower() for report in reports)
    rule_counts = {
        rule.id: {"passed": 0, "failed": 0, "skipped": 0, "errored": 0}
        for rule in RULES
    }
    status_key = {"PASS": "passed", "FAIL": "failed", "SKIP": "skipped", "ERROR": "errored"}
    for report in reports:
        for result in report.results:
            rule_counts[result.rule_id][status_key[result.status]] += 1
    return {
        "schema_version": "0.1.0",
        "total": len(reports),
        "accepted": decisions["accepted"],
        "rejected": decisions["rejected"],
        "invalid": decisions["invalid"],
        "rules": rule_counts,
        "candidates": [
            {
                "candidate_index": report.candidate_index,
                "world_id": report.world_id,
                "decision": report.decision,
                "scientific_validity": report.scientific_validity,
            }
            for report in reports
        ],
    }


def run_experiment(
    config: ResolvedExperimentConfig,
    config_sha256: str,
    out_dir: str | Path,
    count: int | None = None,
    fail_fast_override: bool | None = None,
) -> dict[str, Any]:
    requested = config.experiment.candidate_count if count is None else count
    if requested < 1:
        raise ValueError("count must be at least 1")
    if requested > config.output.max_candidates:
        raise ValueError("count exceeds output.max_candidates")
    if fail_fast_override is not None:
        screening_config = config.screening.model_copy(update={"fail_fast": fail_fast_override})
    else:
        screening_config = config.screening

    destination = Path(out_dir)
    candidate_dir = destination / "candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(destination / "resolved_config.json", config)
    manifest = {
        "schema_version": "0.1.0",
        "project_version": __version__,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "numpy_version": np.__version__,
        "generator_id": config.world_generation.generator.type,
        "generator_version": config.world_generation.generator.version,
        "rng_algorithm": "PCG64",
        "rng_version": np.__version__,
        "constant_set_version": CONSTANT_SET_VERSION,
        "config_sha256": config_sha256,
        "resolved_config": config.model_dump(mode="json"),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        "command_runtime": {"candidate_count": requested, "fail_fast": screening_config.fail_fast},
        "scientific_claim": "initial states only; no long-term stability or habitability claim",
        "platform": sys.platform,
    }
    write_json_atomic(destination / "manifest.json", manifest)

    reports: list[ScreeningReport] = []
    for candidate_index in range(requested):
        try:
            state = generate_initial_state(
                config.world_generation.generator,
                config.experiment.seed,
                candidate_index,
                config_sha256,
            )
        except Exception as exc:
            # Generation failure has no valid state to screen, but always receives a report.
            report = ScreeningReport(
                candidate_index=candidate_index,
                world_id=f"candidate-{candidate_index:06d}",
                decision="INVALID",
                scientific_validity="failed",
                s0_errors=(f"candidate generation failed: {type(exc).__name__}: {exc}",),
                initial_state_diagnostics={},
                results=tuple(
                    {
                        "rule_id": rule.id,
                        "rule_version": "0.1.0",
                        "status": "SKIP",
                        "severity": config.screening.initial_physics.rules[rule.id].severity,
                        "failure_action": (
                            config.screening.initial_physics.rules[rule.id].failure_action
                        ),
                        "observed": {"reason": "candidate generation failed"},
                        "thresholds": config.screening.initial_physics.rules[rule.id].params,
                        "message": "candidate generation failed before S0",
                    }
                    for rule in RULES
                ),
            )
            reports.append(report)
            write_json_atomic(candidate_dir / f"{candidate_index:06d}.screening.json", report)
            continue
        report = screen_candidate(state, screening_config)
        # Serialization failures are experiment failures; do not disguise them as bad candidates.
        write_json_atomic(candidate_dir / f"{candidate_index:06d}.initial.json", state)
        reports.append(report)
        write_json_atomic(candidate_dir / f"{candidate_index:06d}.screening.json", report)
    summary = _summary(reports)
    write_json_atomic(destination / "summary.json", summary)
    return summary

