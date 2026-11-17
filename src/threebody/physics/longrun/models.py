from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from threebody.physics.nbody.models import (
    CollisionSettings,
    EncounterSettings,
    EscapeSettings,
)

RunStatus = Literal[
    "PENDING",
    "RUNNING",
    "PAUSING",
    "PAUSED",
    "CANCELLING",
    "CANCELLED",
    "COMPLETED",
    "COLLISION",
    "FAILED",
]
SegmentStatus = Literal["PLANNED", "WRITING", "COMMITTED", "TERMINAL"]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, allow_inf_nan=False
    )


class LongRunTimeSettings(StrictModel):
    start_time_s: float = Field(allow_inf_nan=False)
    end_time_s: float = Field(allow_inf_nan=False)
    output_interval_s: float = Field(gt=0.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def increasing_window(self) -> LongRunTimeSettings:
        if self.end_time_s <= self.start_time_s:
            raise ValueError("time.end_time_s must be greater than time.start_time_s")
        return self


class SegmentationSettings(StrictModel):
    simulated_duration_s: float = Field(gt=0.0, allow_inf_nan=False)


class CheckpointSettings(StrictModel):
    simulation_interval_s: float = Field(gt=0.0, allow_inf_nan=False)
    wall_interval_s: float | None = Field(default=None, gt=0.0, allow_inf_nan=False)
    retain_count: int = Field(default=3, ge=2, le=10_000)


class ResourceSettings(StrictModel):
    max_wall_seconds_per_invocation: float | None = Field(
        default=None, gt=0.0, allow_inf_nan=False
    )
    max_segments: int = Field(default=2_000, ge=1, le=1_000_000)
    max_samples: int = Field(default=1_000_000, ge=2, le=100_000_000)
    max_checkpoint_bytes: int = Field(
        default=1_073_741_824, ge=1_024, le=1_099_511_627_776
    )


class ResumeSettings(StrictModel):
    require_same_rebound_version: bool = True
    require_same_platform: bool = True
    verify_all_committed_hashes: bool = True


class LongRunSettings(StrictModel):
    schema_version: Literal["0.6.0"] = "0.6.0"
    backend: Literal["rebound_ias15"] = "rebound_ias15"
    time: LongRunTimeSettings
    segmentation: SegmentationSettings
    checkpoint: CheckpointSettings
    resources: ResourceSettings
    resume: ResumeSettings
    epsilon: float = Field(
        default=1.0e-9, ge=1.0e-15, le=1.0e-4, allow_inf_nan=False
    )
    collision: CollisionSettings = Field(default_factory=CollisionSettings)
    encounter: EncounterSettings
    escape: EscapeSettings
    require_positive_radius: bool = False
    softening_m: Literal[0.0] = 0.0

    @model_validator(mode="after")
    def bounded_plan(self) -> LongRunSettings:
        from threebody.physics.longrun.clock import PhysicalClock

        clock = PhysicalClock(self)
        if clock.output_count > self.resources.max_samples:
            raise ValueError(
                f"long run requires {clock.output_count} samples, exceeding "
                f"resources.max_samples={self.resources.max_samples}"
            )
        if clock.segment_count > self.resources.max_segments:
            raise ValueError(
                f"long run requires {clock.segment_count} segments, exceeding "
                f"resources.max_segments={self.resources.max_segments}"
            )
        if (
            self.checkpoint.simulation_interval_s
            > self.segmentation.simulated_duration_s
        ):
            raise ValueError(
                "checkpoint.simulation_interval_s must not exceed segment duration"
            )
        return self


class ContractHashes(StrictModel):
    physics_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schedule_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SegmentIndexEntry(StrictModel):
    segment_index: int = Field(ge=0)
    t_start_s: float = Field(allow_inf_nan=False)
    t_end_s: float = Field(allow_inf_nan=False)
    owns_start_sample: bool
    first_output_index: int | None = Field(default=None, ge=0)
    last_output_index: int | None = Field(default=None, ge=0)
    status: SegmentStatus = "PLANNED"
    previous_segment_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    segment_manifest_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def valid_interval_and_indices(self) -> SegmentIndexEntry:
        if self.t_end_s <= self.t_start_s:
            raise ValueError("segment end must be after segment start")
        if (self.first_output_index is None) != (self.last_output_index is None):
            raise ValueError("segment output indices must both be present or absent")
        if (
            self.first_output_index is not None
            and self.last_output_index < self.first_output_index
        ):
            raise ValueError("segment output index range is inverted")
        if self.segment_index == 0 and not self.owns_start_sample:
            raise ValueError("segment zero must own the global start sample")
        if self.segment_index > 0 and self.owns_start_sample:
            raise ValueError("only segment zero may own its start sample")
        return self


class RunTaskState(StrictModel):
    run_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    status: RunStatus = "PENDING"
    revision: int = Field(default=0, ge=0)
    simulation_time_s: float = Field(allow_inf_nan=False)
    segment_index: int = Field(default=0, ge=0)
    next_output_index: int = Field(default=0, ge=0)
    reason_code: str | None = Field(default=None, max_length=128)


class CheckpointManifest(StrictModel):
    schema_version: Literal["0.6.0"] = "0.6.0"
    run_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    checkpoint_index: int = Field(ge=0)
    simulation_time_s: float = Field(allow_inf_nan=False)
    segment_index: int = Field(ge=0)
    next_output_index: int = Field(ge=0)
    hashes: ContractHashes
    initial_state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rebound_binary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rebound_binary_bytes: int = Field(gt=0)
    rebound_version: str = Field(min_length=1)
    project_version: str = Field(min_length=1)
    python_version: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    body_ids: tuple[str, str, str, str]
    gravitational_constant_si: float = Field(gt=0.0, allow_inf_nan=False)
    integrator: Literal["ias15"] = "ias15"
    epsilon: float = Field(ge=1.0e-15, le=1.0e-4, allow_inf_nan=False)

    @model_validator(mode="after")
    def canonical_bodies(self) -> CheckpointManifest:
        if self.body_ids != ("star-1", "star-2", "star-3", "planet-1"):
            raise ValueError("checkpoint body IDs must use canonical four-body order")
        return self


class LongRunManifest(StrictModel):
    schema_version: Literal["0.6.0"] = "0.6.0"
    run_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    world_id: str = Field(min_length=1)
    initial_state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hashes: ContractHashes
    task: RunTaskState
    segments: tuple[SegmentIndexEntry, ...]

    @model_validator(mode="after")
    def consistent_run_and_segments(self) -> LongRunManifest:
        if self.task.run_id != self.run_id:
            raise ValueError("task run_id does not match manifest")
        if tuple(item.segment_index for item in self.segments) != tuple(
            range(len(self.segments))
        ):
            raise ValueError("segment indices must be contiguous from zero")
        for first, second in zip(self.segments, self.segments[1:], strict=False):
            if not math.isclose(first.t_end_s, second.t_start_s, abs_tol=0.0):
                raise ValueError("segment physical coverage must be contiguous")
        return self
