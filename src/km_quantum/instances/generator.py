"""Versioned, deterministic synthetic fixed-slot workforce instances.

``compatibility_density`` is the independent probability that a worker owns
each skill. Every generated job requires one uniformly chosen skill. Thus the
expected *skill* compatibility fraction is this parameter; a realized finite
instance can differ and capacity is not part of that fraction.

``overlap_density`` is a construction probability, not a requested pairwise
overlap fraction. Starting with the second job, a Bernoulli draw determines
whether its start equals an earlier job's start. Otherwise it starts at the
current maximum end. All durations are positive: probability zero yields no
overlaps, and probability one makes every job start at zero. Intermediate
values need not equal the observed pairwise overlap fraction.

``workload_tightness`` is target total demand divided by aggregate capacity.
For a positive value each worker receives ceil(total demand / (workers * value))
minutes, capped at the horizon. Zero disables workload tightening. Values above
one deliberately produce insufficient aggregate capacity, subject to integer
rounding. Skill and temporal conflicts can prevent full coverage at any value.

The priority distribution contains nonnegative relative weights: entry k-1 is
the sampling weight of integer priority k. The weights need not sum to one.
Workers are available throughout the horizon; split availability is exercised
by the hand cases. The optional explicit horizon must support even the fully
sequential construction. IDs hash the complete normalized configuration and
generator version. No wall-clock value or global random state is used.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from fractions import Fraction
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from km_quantum.domain import Job, SchedulingInstance, TimeWindow, Worker

GENERATOR_VERSION = "fixed-slot-v1"


class GeneratorConfig(BaseModel):
    """Validated parameters for the explicitly defined synthetic distribution."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    number_of_workers: int = Field(default=3, ge=0, strict=True)
    number_of_jobs: int = Field(default=4, ge=0, strict=True)
    number_of_skills: int = Field(default=2, ge=1, strict=True)
    compatibility_density: float = Field(default=0.75, ge=0, le=1, allow_inf_nan=False)
    overlap_density: float = Field(default=0.25, ge=0, le=1, allow_inf_nan=False)
    workload_tightness: float = Field(default=0.5, ge=0, allow_inf_nan=False)
    priority_distribution: tuple[float, ...] = (0.6, 0.3, 0.1)
    seed: int = Field(default=0, ge=0, strict=True)
    min_duration_minutes: int = Field(default=30, gt=0, strict=True)
    max_duration_minutes: int = Field(default=60, gt=0, strict=True)
    horizon_minutes: int | None = Field(default=None, gt=0, strict=True)
    must_assign_fraction: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    min_cost_per_hour: int | None = Field(default=None, ge=0, strict=True)
    max_cost_per_hour: int | None = Field(default=None, ge=0, strict=True)

    @field_validator("priority_distribution")
    @classmethod
    def validate_priorities(cls, weights: tuple[float, ...]) -> tuple[float, ...]:
        if not weights or any(not math.isfinite(weight) or weight < 0 for weight in weights):
            raise ValueError("priority_distribution must contain finite nonnegative weights")
        if not any(weight > 0 for weight in weights):
            raise ValueError("priority_distribution needs a positive weight")
        if not math.isfinite(sum(weights)):
            raise ValueError("priority_distribution total must be finite")
        return weights

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.min_duration_minutes > self.max_duration_minutes:
            raise ValueError("min_duration_minutes must not exceed max_duration_minutes")
        needed_horizon = max(1, self.number_of_jobs) * self.max_duration_minutes
        if self.horizon_minutes is not None and self.horizon_minutes < needed_horizon:
            raise ValueError(
                "horizon_minutes must be at least max(1, number_of_jobs) * "
                "max_duration_minutes to permit the sequential construction"
            )
        if (self.min_cost_per_hour is None) != (self.max_cost_per_hour is None):
            raise ValueError("provide both min_cost_per_hour and max_cost_per_hour, or neither")
        if (
            self.min_cost_per_hour is not None
            and self.max_cost_per_hour is not None
            and self.min_cost_per_hour > self.max_cost_per_hour
        ):
            raise ValueError("min_cost_per_hour must not exceed max_cost_per_hour")
        return self


def generate_instance(config: GeneratorConfig) -> SchedulingInstance:
    """Generate identical instance data and identity for an identical configuration."""
    parameters = config.model_dump(mode="json")
    digest_input = json.dumps(
        {"generator_version": GENERATOR_VERSION, "parameters": parameters},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    instance_id = "synthetic-" + hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:20]
    rng = random.Random(config.seed)
    skills = tuple(f"skill-{index:02d}" for index in range(config.number_of_skills))
    worker_skills = tuple(
        frozenset(skill for skill in skills if rng.random() < config.compatibility_density)
        for _ in range(config.number_of_workers)
    )
    horizon = config.horizon_minutes or max(1, config.number_of_jobs) * config.max_duration_minutes
    jobs: list[Job] = []
    frontier = 0
    shared_starts = 0
    for index in range(config.number_of_jobs):
        duration = rng.randint(config.min_duration_minutes, config.max_duration_minutes)
        if jobs and rng.random() < config.overlap_density:
            start = jobs[rng.randrange(len(jobs))].start_minute
            shared_starts += 1
        else:
            start = frontier
        job = Job(
            id=f"job-{index:03d}",
            required_skills=frozenset((rng.choice(skills),)),
            start_minute=start,
            end_minute=start + duration,
            priority=rng.choices(
                range(1, len(config.priority_distribution) + 1),
                weights=config.priority_distribution,
            )[0],
            must_assign=rng.random() < config.must_assign_fraction,
        )
        jobs.append(job)
        frontier = max(frontier, job.end_minute)

    demand = sum(job.duration_minutes for job in jobs)
    capacity = horizon
    if config.number_of_workers and config.workload_tightness > 0:
        # Decimal-to-rational conversion avoids overflow for tiny positive values.
        capacity = min(
            horizon,
            math.ceil(
                Fraction(demand, config.number_of_workers)
                / Fraction(str(config.workload_tightness))
            ),
        )
    workers = tuple(
        Worker(
            id=f"worker-{index:03d}",
            skills=owned_skills,
            availability=(TimeWindow(start_minute=0, end_minute=horizon),),
            max_work_minutes=capacity,
            cost_per_hour=(
                rng.randint(config.min_cost_per_hour, config.max_cost_per_hour)
                if config.min_cost_per_hour is not None and config.max_cost_per_hour is not None
                else None
            ),
        )
        for index, owned_skills in enumerate(worker_skills)
    )
    pairs = config.number_of_workers * config.number_of_jobs
    compatible_pairs = sum(
        job.required_skills <= worker.skills for job in jobs for worker in workers
    )
    temporal_pairs = config.number_of_jobs * (config.number_of_jobs - 1) // 2
    overlapping_pairs = sum(
        left.start_minute < right.end_minute and right.start_minute < left.end_minute
        for index, left in enumerate(jobs)
        for right in jobs[index + 1 :]
    )
    return SchedulingInstance(
        instance_id=instance_id,
        seed=config.seed,
        workers=workers,
        jobs=tuple(jobs),
        generator_parameters=parameters,
        metadata={
            "source": "synthetic",
            "generator_version": GENERATOR_VERSION,
            "effective_horizon_minutes": horizon,
            "total_job_minutes": demand,
            "worker_capacity_minutes": capacity,
            "shared_start_constructions": shared_starts,
            "observed_skill_compatibility_density": compatible_pairs / pairs if pairs else 0.0,
            "observed_pairwise_overlap_density": (
                overlapping_pairs / temporal_pairs if temporal_pairs else 0.0
            ),
            "observed_workload_tightness": (
                demand / (capacity * len(workers)) if capacity and workers else None
            ),
        },
    )
