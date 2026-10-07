"""Validated JSONL benchmark records with enough context to repeat a run."""

from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from km_quantum import __version__
from km_quantum.domain import SchedulingInstance, SolverConfig, SolverResult
from km_quantum.experiments.io import canonical_hash
from km_quantum.metrics import evaluate_objective, validate_schedule
from km_quantum.solvers.classical import cp_sat, exhaustive

SolverName = Literal["cp-sat", "exhaustive"]


class BenchmarkResult(BaseModel):
    """One immutable result; null solution metrics mean no incumbent was returned."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    experiment_id: str = Field(min_length=1)
    instance_id: str
    instance_hash: str
    seed: int | None
    solver: SolverName
    solver_version: str
    status: Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN", "MODEL_INVALID"]
    feasible: bool
    objective: int | None = Field(ge=0)
    best_bound: float | None = Field(allow_inf_nan=False)
    optimality_gap: float | None = Field(ge=0, allow_inf_nan=False)
    runtime_ms: float = Field(ge=0, allow_inf_nan=False)
    proven_optimal: bool
    number_workers: int = Field(ge=0)
    number_jobs: int = Field(ge=0)
    assignment_count: int | None = Field(default=None, ge=0)
    unassigned_count: int | None = Field(default=None, ge=0)
    violations: tuple[dict[str, Any], ...]
    timestamp: AwareDatetime
    git_commit: str | None
    git_dirty: bool | None
    dependency_lock_sha256: str | None
    environment: dict[str, str | None]
    instance: dict[str, Any]
    solver_config: dict[str, Any]
    raw_result: dict[str, Any]
    objective_breakdown: dict[str, Any] | None

    @model_validator(mode="after")
    def consistent_solution_metrics(self) -> Self:
        if self.proven_optimal != (self.status == "OPTIMAL"):
            raise ValueError("proven_optimal must agree with OPTIMAL status")
        if self.proven_optimal and not self.feasible:
            raise ValueError("A proven optimum requires an independently feasible schedule")
        if self.feasible:
            if self.status not in ("OPTIMAL", "FEASIBLE") or self.violations:
                raise ValueError("A feasible record requires a solution status and no violations")
            if (
                self.objective is None
                or self.assignment_count is None
                or self.unassigned_count is None
                or self.objective_breakdown is None
            ):
                raise ValueError("A feasible record requires complete solution metrics")
            if self.assignment_count + self.unassigned_count != self.number_jobs:
                raise ValueError("Coverage counts must account for every job")
        elif self.objective is not None or self.objective_breakdown is not None:
            raise ValueError("An independently infeasible record cannot claim an objective")
        return self


def solve_instance(
    instance: SchedulingInstance,
    config: SolverConfig,
    solver: SolverName = "cp-sat",
) -> SolverResult:
    if solver == "cp-sat":
        return cp_sat.solve(instance, config)
    if solver == "exhaustive":
        return exhaustive.solve(instance, config)
    raise ValueError(f"Unknown solver: {solver}")


def _version(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def _repository_context(repository: Path) -> tuple[str | None, bool | None, Path]:
    """Git state is optional: installed wheels can run outside a checkout."""
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=top,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=top,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        return commit, bool(dirty), Path(top)
    except (OSError, subprocess.SubprocessError):
        return None, None, repository


def run_benchmark(
    instance: SchedulingInstance,
    config: SolverConfig | None = None,
    *,
    experiment_id: str,
    solver: SolverName = "cp-sat",
    repository: str | Path | None = None,
) -> BenchmarkResult:
    """Solve once, independently validate and score, then capture provenance."""
    if not experiment_id.strip():
        raise ValueError("experiment_id must not be blank")
    config = config or SolverConfig()
    result = solve_instance(instance, config, solver)
    schedule = result.schedule
    validation = validate_schedule(instance, schedule) if schedule is not None else None
    feasible = validation is not None and validation.is_valid
    objective = (
        evaluate_objective(instance, schedule, config.objective)
        if feasible and schedule is not None
        else None
    )
    if objective is not None and objective.total != result.objective_value:
        raise ValueError("Solver objective disagrees with the independent objective evaluator")
    if result.proven_optimal and not feasible:
        raise ValueError("A proven-optimal result must contain an independently valid schedule")
    commit, dirty, root = _repository_context(Path(repository) if repository else Path.cwd())
    lock_path = root / "uv.lock"
    lock_hash = hashlib.sha256(lock_path.read_bytes()).hexdigest() if lock_path.is_file() else None
    versions = {
        "km-quantum": _version("km-quantum") or __version__,
        "ortools": _version("ortools"),
        "pydantic": _version("pydantic"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }
    implementation_version = result.solver_metadata.get("solver_version")
    solver_version = (
        implementation_version
        if isinstance(implementation_version, str)
        else (versions["ortools"] if solver == "cp-sat" else versions["km-quantum"]) or "unknown"
    )
    return BenchmarkResult(
        experiment_id=experiment_id,
        instance_id=instance.instance_id,
        instance_hash=canonical_hash(instance),
        seed=instance.seed,
        solver=solver,
        solver_version=solver_version,
        status=result.status,
        feasible=feasible,
        objective=objective.total if objective is not None else None,
        best_bound=result.best_bound,
        optimality_gap=result.optimality_gap,
        runtime_ms=result.runtime_ms,
        proven_optimal=result.proven_optimal,
        number_workers=len(instance.workers),
        number_jobs=len(instance.jobs),
        assignment_count=len(schedule.assignments) if schedule is not None else None,
        unassigned_count=len(schedule.unassigned_job_ids) if schedule is not None else None,
        violations=tuple(
            violation.model_dump(mode="json") for violation in validation.violations
        )
        if validation is not None
        else (),
        timestamp=datetime.now(UTC),
        git_commit=commit,
        git_dirty=dirty,
        dependency_lock_sha256=lock_hash,
        environment=versions,
        instance=instance.model_dump(mode="json"),
        solver_config=config.model_dump(mode="json"),
        raw_result=result.model_dump(mode="json"),
        objective_breakdown=objective.model_dump(mode="json") if objective is not None else None,
    )


def append_benchmark(path: str | Path, record: BenchmarkResult) -> None:
    """Append one JSON object per line. This writer is intended for serial runs."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(record.model_dump_json() + "\n")
