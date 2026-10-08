"""M2 equivalence records; the frozen M1 benchmark schema remains unchanged."""

from __future__ import annotations

import hashlib
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from km_quantum import __version__
from km_quantum.domain import FrozenModel, SchedulingInstance
from km_quantum.experiments.benchmark import _repository_context, _version
from km_quantum.experiments.io import canonical_hash
from km_quantum.qubo.decoder import decode_bitstring
from km_quantum.qubo.encoder import encode_qubo, instance_fingerprint
from km_quantum.qubo.ising import qubo_to_ising
from km_quantum.qubo.models import QUBOModel, evaluate_qubo_energy
from km_quantum.qubo.verification import VerificationResult


class M2BenchmarkResult(FrozenModel):
    schema_version: Literal[2] = 2
    artifact_type: Literal["m2_equivalence"] = "m2_equivalence"
    experiment_id: str = Field(min_length=1)
    instance_id: str
    seed: int
    instance_hash: str
    qubo_hash: str
    status: Literal["PASS", "FAIL", "INFEASIBLE", "INCONCLUSIVE"]
    equivalence_passed: bool
    ising_equivalence_passed: bool
    number_workers: int = Field(ge=0)
    number_jobs: int = Field(ge=0)
    original_worker_job_combinations: int = Field(ge=0)
    assignment_variable_count: int = Field(ge=0)
    unassigned_variable_count: int = Field(ge=0)
    slack_variable_count: int = Field(ge=0)
    auxiliary_variable_count: int = Field(ge=0)
    qubo_variable_count: int = Field(ge=0)
    linear_term_count: int = Field(ge=0)
    quadratic_term_count: int = Field(ge=0)
    qubo_density: float = Field(ge=0, le=1, allow_inf_nan=False)
    preprocessing_reduction: int = Field(ge=0)
    reduction_ratio: float = Field(ge=0, le=1, allow_inf_nan=False)
    max_coefficient: int = Field(ge=0)
    min_nonzero_coefficient: int | None = Field(ge=1)
    coefficient_dynamic_range: str | None
    penalty_configuration: dict[str, Any]
    ground_energy: int
    ground_state_count: int = Field(ge=1)
    valid_ground_state_count: int = Field(ge=0)
    invalid_ground_state_count: int = Field(ge=0)
    best_invalid_energy: int | None
    best_valid_energy: int | None
    constraint_energy_margin: int | None
    first_excited_energy: int | None
    energy_gap: int | None = Field(ge=0)
    classical_objective: int | None = Field(ge=0)
    decoded_qubo_objective: int | None = Field(ge=0)
    enumerated_bitstrings: int = Field(ge=1)
    runtime_ms: float = Field(ge=0, allow_inf_nan=False)
    timestamp: AwareDatetime
    git_commit: str | None
    git_dirty: bool | None
    dependency_lock_sha256: str | None
    environment: dict[str, str | None]
    metrics: dict[str, Any]
    instance: dict[str, Any]
    qubo_config: dict[str, Any]
    qubo_model: dict[str, Any]
    raw_result: dict[str, Any]

    @model_validator(mode="after")
    def coherent_equivalence(self) -> Self:
        if (
            self.valid_ground_state_count + self.invalid_ground_state_count
            != self.ground_state_count
        ):
            raise ValueError("Ground-state counts must cover all minima")
        if self.equivalence_passed != (self.status == "PASS"):
            raise ValueError("Equivalence flag must agree with PASS status")
        if self.equivalence_passed and (
            not self.ising_equivalence_passed
            or self.invalid_ground_state_count
            or self.classical_objective is None
            or self.decoded_qubo_objective != self.classical_objective
        ):
            raise ValueError(
                "PASS requires valid ground states, Ising equality, and oracle equality"
            )
        return self


def build_m2_benchmark(
    instance: SchedulingInstance,
    model: QUBOModel,
    verification: VerificationResult,
    *,
    experiment_id: str,
    repository: str | Path | None = None,
) -> M2BenchmarkResult:
    if not experiment_id.strip():
        raise ValueError("experiment_id must not be blank")
    if (
        model.mapping.instance_id != instance.instance_id
        or model.mapping.instance_hash != instance_fingerprint(instance)
    ):
        raise ValueError("Benchmark model does not match the supplied instance")
    if model != encode_qubo(instance, model.config):
        raise ValueError("Benchmark model differs from the canonical encoding")
    if verification.ising_result.ising_model != qubo_to_ising(model):
        raise ValueError("Verification Ising model does not match the benchmark QUBO")
    result = verification.qubo_result
    if result.state_count != 1 << model.number_variables:
        raise ValueError("Verification state dimension does not match the benchmark QUBO")
    for ground in result.ground_states:
        if ground.energy != model.energy(ground.bits) or ground.breakdown != evaluate_qubo_energy(
            model, ground.bits
        ):
            raise ValueError("Verification ground energy does not match the benchmark QUBO")
        decoded_schedule = decode_bitstring(instance, model.mapping, ground.bits)
        if (
            decoded_schedule.instance_id != ground.schedule.instance_id
            or decoded_schedule.assignments != ground.schedule.assignments
            or decoded_schedule.unassigned_job_ids != ground.schedule.unassigned_job_ids
        ):
            raise ValueError("Verification ground schedule differs from the benchmark decoder")
    metrics = model.metrics()
    valid_ground_states = [
        state
        for state in result.ground_states
        if state.encoded_feasible and state.validation.is_valid and state.objective_matches
    ]
    objectives = {state.business_objective for state in valid_ground_states}
    decoded = next(iter(objectives)) if len(objectives) == 1 else None
    commit, dirty, root = _repository_context(Path(repository) if repository else Path.cwd())
    lock_path = root / "uv.lock"
    lock_hash = hashlib.sha256(lock_path.read_bytes()).hexdigest() if lock_path.is_file() else None
    return M2BenchmarkResult(
        experiment_id=experiment_id,
        instance_id=instance.instance_id,
        seed=instance.seed,
        instance_hash=canonical_hash(instance),
        qubo_hash=canonical_hash(model),
        status=verification.status,
        equivalence_passed=verification.equivalence_passed,
        ising_equivalence_passed=verification.ising_result.equivalent,
        number_workers=len(instance.workers),
        number_jobs=len(instance.jobs),
        original_worker_job_combinations=metrics.original_worker_job_combinations,
        assignment_variable_count=metrics.assignment_variable_count,
        unassigned_variable_count=metrics.unassigned_variable_count,
        slack_variable_count=metrics.slack_variable_count,
        auxiliary_variable_count=metrics.auxiliary_variable_count,
        qubo_variable_count=metrics.qubo_variable_count,
        linear_term_count=metrics.linear_term_count,
        quadratic_term_count=metrics.quadratic_term_count,
        qubo_density=metrics.qubo_density,
        preprocessing_reduction=metrics.preprocessing_reduction,
        reduction_ratio=metrics.reduction_ratio,
        max_coefficient=metrics.max_coefficient,
        min_nonzero_coefficient=metrics.min_nonzero_coefficient,
        coefficient_dynamic_range=metrics.coefficient_dynamic_range,
        penalty_configuration=model.penalties.model_dump(mode="json"),
        ground_energy=result.ground_energy,
        ground_state_count=result.ground_state_degeneracy,
        valid_ground_state_count=len(valid_ground_states),
        invalid_ground_state_count=result.ground_state_degeneracy - len(valid_ground_states),
        best_invalid_energy=result.best_invalid_energy,
        best_valid_energy=result.best_valid_energy,
        constraint_energy_margin=result.invalid_energy_margin,
        first_excited_energy=result.first_excited_energy,
        energy_gap=result.first_excited_gap,
        classical_objective=verification.classical_objective,
        decoded_qubo_objective=decoded,
        enumerated_bitstrings=result.explored_states,
        runtime_ms=verification.runtime_ms,
        timestamp=datetime.now(UTC),
        git_commit=commit,
        git_dirty=dirty,
        dependency_lock_sha256=lock_hash,
        environment={
            "km-quantum": _version("km-quantum") or __version__,
            "ortools": _version("ortools"),
            "pydantic": _version("pydantic"),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "encoder": "m2-v1",
        },
        metrics=metrics.model_dump(mode="json"),
        instance=instance.model_dump(mode="json"),
        qubo_config=model.config.model_dump(mode="json"),
        qubo_model=model.model_dump(mode="json"),
        raw_result=verification.model_dump(mode="json"),
    )


def append_m2_benchmark(path: str | Path, record: M2BenchmarkResult) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(record.model_dump_json() + "\n")
