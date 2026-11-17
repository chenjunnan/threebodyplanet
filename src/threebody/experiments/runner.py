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
from threebody.world.generators import generate_initial_state_with_metadata
from threebody.world.stochastic_conics import (
    ConstraintExhausted,
    GenerationError,
    GenerationMetadata,
    GenerationMetadataV041,
)

GenerationRecord = GenerationMetadata | GenerationMetadataV041


def _range_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "max": None, "mean": None}
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": sum(values) / len(values),
    }


def _summary(
    reports: list[ScreeningReport],
    generation_records: list[GenerationRecord] | None = None,
    generation_errors: Counter[str] | None = None,
    initial_contact_counts: dict[int, int] | None = None,
    generation_reason_codes: dict[int, str] | None = None,
    constraint_attempt_counts: list[int] | None = None,
    constraint_rejection_counts: Counter[str] | None = None,
) -> dict[str, Any]:
    decisions = Counter(report.decision.lower() for report in reports)
    rule_counts = {
        rule.id: {"passed": 0, "failed": 0, "skipped": 0, "errored": 0}
        for rule in RULES
    }
    status_key = {"PASS": "passed", "FAIL": "failed", "SKIP": "skipped", "ERROR": "errored"}
    for report in reports:
        for result in report.results:
            rule_counts[result.rule_id][status_key[result.status]] += 1
    summary: dict[str, Any] = {
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
    if generation_records is not None:
        hosts = Counter(record.selected_planet_host for record in generation_records)
        conic_types: dict[str, Counter[str]] = {
            relation: Counter() for relation in ("inner", "outer", "planet")
        }
        eccentricities: dict[str, list[float]] = {
            relation: [] for relation in conic_types
        }
        distances: dict[str, list[float]] = {relation: [] for relation in conic_types}
        speeds: dict[str, list[float]] = {relation: [] for relation in conic_types}
        for metadata in generation_records:
            for conic in metadata.conics:
                conic_types[conic.relation][conic.conic_type] += 1
                eccentricities[conic.relation].append(conic.eccentricity)
                distances[conic.relation].append(conic.radius_m)
                speeds[conic.relation].append(
                    float(np.linalg.norm(conic.relative_velocity_post_kick_mps))
                )
        contacts = initial_contact_counts or {}
        reason_codes = generation_reason_codes or {}
        constraint_rejections = constraint_rejection_counts or Counter()
        attempt_counts = constraint_attempt_counts or []
        metadata_version = (
            generation_records[0].schema_version if generation_records else "0.4.1"
        )
        summary["g2_generation"] = {
            "schema_version": metadata_version,
            "generated": len(generation_records),
            "generation_invalid": sum((generation_errors or Counter()).values()),
            "generation_invalid_reason_codes": dict(sorted((generation_errors or {}).items())),
            "selected_planet_hosts": {name: hosts[name] for name in sorted(hosts)},
            "conic_types": {
                relation: dict(sorted(counts.items()))
                for relation, counts in conic_types.items()
            },
            "eccentricity": {
                relation: _range_summary(values)
                for relation, values in eccentricities.items()
            },
            "instantaneous_distance_m": {
                relation: _range_summary(values) for relation, values in distances.items()
            },
            "post_kick_relative_speed_mps": {
                relation: _range_summary(values) for relation, values in speeds.items()
            },
            "initial_terminal_worlds": sum(count > 0 for count in contacts.values()),
            "initial_contact_pairs": sum(contacts.values()),
            "constraint_attempts": _range_summary(
                [float(value) for value in attempt_counts]
            ),
            "constraint_rejection_reason_codes": dict(sorted(constraint_rejections.items())),
            "candidate_outcomes": [
                {
                    "candidate_index": report.candidate_index,
                    "generation_reason_code": reason_codes.get(
                        report.candidate_index, "GENERATION_EXCEPTION"
                    ),
                    "initial_terminal": contacts.get(report.candidate_index, 0) > 0,
                    "initial_contact_count": contacts.get(report.candidate_index, 0),
                    "screening_decision": report.decision,
                }
                for report in reports
            ],
            "scientific_claim": (
                "initial states only; no generated candidate is claimed stable, surviving, "
                "or habitable"
            ),
        }
    return summary


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
    is_g2 = config.world_generation.generator.type == "stochastic_conics"
    generation_records: list[GenerationRecord] = []
    generation_errors: Counter[str] = Counter()
    initial_contact_counts: dict[int, int] = {}
    generation_reason_codes: dict[int, str] = {}
    constraint_attempt_counts: list[int] = []
    constraint_rejection_counts: Counter[str] = Counter()
    for candidate_index in range(requested):
        try:
            state, generation_metadata, initial_contacts = generate_initial_state_with_metadata(
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
            if is_g2:
                reason_code = (
                    exc.reason_code if isinstance(exc, GenerationError) else "GENERATION_EXCEPTION"
                )
                generation_errors[reason_code] += 1
                generation_reason_codes[candidate_index] = reason_code
                if isinstance(exc, ConstraintExhausted):
                    constraint_attempt_counts.append(exc.metadata["attempt_count"])
                    for attempt in exc.metadata["attempts"]:
                        constraint_rejection_counts.update(attempt["reason_codes"])
                failure_metadata = (
                    exc.metadata
                    if isinstance(exc, ConstraintExhausted)
                    else {
                        "schema_version": "0.4.0",
                        "generator_id": "stochastic_conics",
                        "generator_version": "0.4.0",
                        "candidate_index": candidate_index,
                        "status": "INVALID",
                        "reason_code": reason_code,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }
                )
                write_json_atomic(
                    candidate_dir / f"{candidate_index:06d}.generation_metadata.json",
                    failure_metadata,
                )
            continue
        if is_g2:
            assert isinstance(generation_metadata, (GenerationMetadata, GenerationMetadataV041))
            generation_records.append(generation_metadata)
            if isinstance(generation_metadata, GenerationMetadataV041):
                constraint_attempt_counts.append(generation_metadata.attempt_count)
                for attempt in generation_metadata.attempts:
                    constraint_rejection_counts.update(attempt.reason_codes)
            generation_reason_codes[candidate_index] = "GENERATED"
            initial_contact_counts[candidate_index] = len(initial_contacts)
            write_json_atomic(
                candidate_dir / f"{candidate_index:06d}.generation_metadata.json",
                generation_metadata,
            )
            if initial_contacts:
                write_json_atomic(
                    candidate_dir / f"{candidate_index:06d}.initial_outcome.json",
                    initial_contacts,
                )
        report = screen_candidate(state, screening_config)
        # Serialization failures are experiment failures; do not disguise them as bad candidates.
        write_json_atomic(candidate_dir / f"{candidate_index:06d}.initial.json", state)
        reports.append(report)
        write_json_atomic(candidate_dir / f"{candidate_index:06d}.screening.json", report)
    summary = _summary(
        reports,
        generation_records if is_g2 else None,
        generation_errors if is_g2 else None,
        initial_contact_counts if is_g2 else None,
        generation_reason_codes if is_g2 else None,
        constraint_attempt_counts if is_g2 else None,
        constraint_rejection_counts if is_g2 else None,
    )
    write_json_atomic(destination / "summary.json", summary)
    return summary

