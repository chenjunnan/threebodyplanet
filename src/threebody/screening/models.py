from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class RuleResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    rule_id: str
    rule_version: str
    status: Literal["PASS", "FAIL", "SKIP", "ERROR"]
    severity: Literal["error", "warning", "info"]
    failure_action: Literal["reject", "report"]
    observed: dict[str, Any]
    thresholds: dict[str, Any]
    message: str


class ScreeningReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: str = "0.1.0"
    candidate_index: int
    world_id: str
    decision: Literal["ACCEPTED", "REJECTED", "INVALID"]
    scientific_validity: Literal["screened", "unverified", "failed"]
    s0_errors: tuple[str, ...]
    initial_state_diagnostics: dict[str, Any]
    results: tuple[RuleResult, ...]

