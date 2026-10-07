"""Validated input/output schemas; times and objective coefficients are integers."""

from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_serializer,
    model_validator,
)

Identifier = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
NonnegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)


class TimeWindow(FrozenModel):
    """A separate half-open available period [start_minute, end_minute)."""

    start_minute: NonnegativeInt
    end_minute: NonnegativeInt

    @model_validator(mode="after")
    def positive_duration(self) -> Self:
        if self.end_minute <= self.start_minute:
            raise ValueError("end_minute must be greater than start_minute")
        return self


class Worker(FrozenModel):
    id: Identifier
    skills: frozenset[Identifier]
    availability: tuple[TimeWindow, ...]
    max_work_minutes: NonnegativeInt
    # Integer minor monetary units per hour, e.g. 2400 = EUR 24.00/hour.
    cost_per_hour: NonnegativeInt | None = None

    @field_serializer("skills")
    def sorted_skills(self, value: frozenset[str]) -> list[str]:
        return sorted(value)

    @model_validator(mode="after")
    def distinct_periods(self) -> Self:
        periods = sorted(self.availability, key=lambda period: period.start_minute)
        if any(
            left.end_minute > right.start_minute
            for left, right in zip(periods, periods[1:], strict=False)
        ):
            raise ValueError("availability periods must not overlap")
        return self


class Job(FrozenModel):
    id: Identifier
    required_skills: frozenset[Identifier]
    start_minute: NonnegativeInt
    end_minute: NonnegativeInt
    priority: PositiveInt = 1
    unassigned_penalty: NonnegativeInt | None = None
    must_assign: bool = Field(default=False, strict=True)

    @field_serializer("required_skills")
    def sorted_skills(self, value: frozenset[str]) -> list[str]:
        return sorted(value)

    @model_validator(mode="after")
    def positive_duration(self) -> Self:
        if self.end_minute <= self.start_minute:
            raise ValueError("end_minute must be greater than start_minute")
        return self

    @property
    def duration_minutes(self) -> int:
        return self.end_minute - self.start_minute


class SchedulingInstance(FrozenModel):
    instance_id: Identifier
    seed: NonnegativeInt
    workers: tuple[Worker, ...]
    jobs: tuple[Job, ...]
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    generator_parameters: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        for name, entities in (("worker", self.workers), ("job", self.jobs)):
            identifiers = [entity.id for entity in entities]
            if len(set(identifiers)) != len(identifiers):
                raise ValueError(f"duplicate {name} IDs")
        return self


class Assignment(FrozenModel):
    worker_id: Identifier
    job_id: Identifier


class Schedule(FrozenModel):
    """Every job must occur exactly once in assignments or unassigned_job_ids.

    Cross-reference and feasibility checks intentionally belong to the independent
    validator, allowing invalid solver candidates to be inspected and reported.
    """

    instance_id: Identifier
    assignments: tuple[Assignment, ...] = ()
    unassigned_job_ids: tuple[Identifier, ...] = ()
    objective_value: NonnegativeInt | None = None
    solver_metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ObjectiveWeights(FrozenModel):
    """Integer weighted-sum objective (not an implicit lexicographic order)."""

    unassigned_penalty: NonnegativeInt = 100_000
    cost_weight: NonnegativeInt = 0
    imbalance_weight: NonnegativeInt = 0


class SolverConfig(FrozenModel):
    objective: ObjectiveWeights = Field(default_factory=ObjectiveWeights)
    time_limit_seconds: float = Field(default=10.0, gt=0, allow_inf_nan=False)
    num_search_workers: PositiveInt = Field(default=1, le=2**31 - 1)
    random_seed: NonnegativeInt = Field(default=0, le=2**31 - 1)
    max_deterministic_time: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    exhaustive_max_states: PositiveInt = 1_000_000


class SolverResult(FrozenModel):
    status: Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN", "MODEL_INVALID"]
    schedule: Schedule | None
    objective_value: NonnegativeInt | None
    runtime_ms: float = Field(ge=0, allow_inf_nan=False)
    proven_optimal: bool
    best_bound: float | None = Field(default=None, allow_inf_nan=False)
    optimality_gap: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    solver_metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def consistent_result(self) -> Self:
        if (self.status in ("OPTIMAL", "FEASIBLE")) != (self.schedule is not None):
            raise ValueError("OPTIMAL/FEASIBLE require a schedule; other statuses forbid one")
        if self.proven_optimal != (self.status == "OPTIMAL"):
            raise ValueError("proven_optimal must agree with OPTIMAL status")
        if self.schedule is None:
            if self.objective_value is not None or self.proven_optimal:
                raise ValueError("a result without a schedule cannot claim an objective or optimum")
        elif self.objective_value is None:
            raise ValueError("a schedule requires an objective_value")
        elif self.schedule.objective_value != self.objective_value:
            raise ValueError("schedule and result objective values must agree")
        return self
