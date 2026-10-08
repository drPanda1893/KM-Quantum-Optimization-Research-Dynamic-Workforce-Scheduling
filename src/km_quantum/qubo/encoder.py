"""Exact fixed-slot QUBO encoding, with derived penalties and bounded auxiliaries."""

import json
import math
from fractions import Fraction

from km_quantum.domain import SchedulingInstance
from km_quantum.experiments.io import canonical_hash
from km_quantum.metrics import assignment_cost, unassigned_cost

from .models import (
    BinaryVariable,
    IntegerEncoding,
    IntegerRole,
    PenaltyMetadata,
    PolynomialBuilder,
    PrunedAssignment,
    PruningReason,
    QUBOConfig,
    QUBOModel,
    VariableKind,
    VariableMapping,
)


def instance_fingerprint(instance: SchedulingInstance) -> str:
    """Canonical input identity, also independent of worker/job/availability order."""
    data = instance.model_dump(mode="json")
    data["workers"].sort(key=lambda item: item["id"])
    data["jobs"].sort(key=lambda item: item["id"])
    for worker in data["workers"]:
        worker["availability"].sort(
            key=lambda period: (period["start_minute"], period["end_minute"])
        )
    return canonical_hash(data)


def eligible_pairs(
    instance: SchedulingInstance,
) -> tuple[list[tuple[str, str]], list[PrunedAssignment]]:
    pairs: list[tuple[str, str]] = []
    removed: list[PrunedAssignment] = []
    for job in sorted(instance.jobs, key=lambda job: job.id):
        for worker in sorted(instance.workers, key=lambda worker: worker.id):
            reason: PruningReason | None = None
            if not job.required_skills <= worker.skills:
                reason = "skill"
            elif not any(
                period.start_minute <= job.start_minute and job.end_minute <= period.end_minute
                for period in worker.availability
            ):
                reason = "availability"
            elif job.duration_minutes > worker.max_work_minutes:
                reason = "individual_capacity"
            if reason is None:
                pairs.append((job.id, worker.id))
            else:
                removed.append(PrunedAssignment(job_id=job.id, worker_id=worker.id, reason=reason))
    return pairs, removed


def bounded_weights(bound: int) -> tuple[int, ...]:
    if type(bound) is not int or bound < 0:
        raise ValueError("bounded integer upper bound must be a nonnegative integer")
    if bound == 0:
        return ()
    count = bound.bit_length()
    return tuple(1 << index for index in range(count - 1)) + (bound - ((1 << (count - 1)) - 1),)


def encode_qubo(instance: SchedulingInstance, config: QUBOConfig | None = None) -> QUBOModel:
    config = config or QUBOConfig()
    jobs = {job.id: job for job in sorted(instance.jobs, key=lambda job: job.id)}
    workers = {
        worker.id: worker for worker in sorted(instance.workers, key=lambda worker: worker.id)
    }
    pairs, removed = eligible_pairs(instance)
    variables: list[BinaryVariable] = []
    integers: list[IntegerEncoding] = []

    def variable(
        name: str,
        kind: VariableKind,
        *,
        job_id: str | None = None,
        worker_id: str | None = None,
        group: str | None = None,
        weight: int = 1,
    ) -> int:
        if len(variables) >= config.max_variables:
            raise ValueError(f"encoding exceeds max_variables={config.max_variables}")
        index = len(variables)
        variables.append(
            BinaryVariable(
                index=index,
                name=name,
                kind=kind,
                job_id=job_id,
                worker_id=worker_id,
                group=group,
                weight=weight,
            )
        )
        return index

    assignment = {
        (job_id, worker_id): variable(
            "x:" + json.dumps([job_id, worker_id]), "assignment", job_id=job_id, worker_id=worker_id
        )
        for job_id, worker_id in pairs
    }
    omitted = {
        job_id: variable("u:" + json.dumps(job_id), "unassigned", job_id=job_id) for job_id in jobs
    }
    raw_loads = {
        worker_id: {
            index: jobs[job_id].duration_minutes
            for (job_id, wi), index in assignment.items()
            if wi == worker_id
        }
        for worker_id in workers
    }
    load_unit = math.gcd(*(jobs[job_id].duration_minutes for job_id, _ in pairs)) or 1
    normalized_loads = {
        worker_id: {index: duration // load_unit for index, duration in load.items()}
        for worker_id, load in raw_loads.items()
    }
    upper_loads = {
        wi: min(worker.max_work_minutes // load_unit, sum(normalized_loads[wi].values()))
        for wi, worker in workers.items()
    }
    horizon = max(upper_loads.values(), default=0)
    imbalance_active = config.objective.imbalance_weight > 0 and len(workers) >= 2 and horizon > 0
    objective_upper = sum(
        max(
            [unassigned_cost(job, config.objective) if not job.must_assign else 0]
            + [
                assignment_cost(workers[wi], job, config.objective)
                for ji, wi in pairs
                if ji == job.id
            ]
        )
        for job in jobs.values()
    )
    if imbalance_active:
        objective_upper += config.objective.imbalance_weight * load_unit * horizon
    safe = objective_upper + config.penalty_margin
    penalty = math.ceil(Fraction(safe) * Fraction(config.penalty_scale))
    penalties = PenaltyMetadata(
        business_upper_bound=objective_upper,
        safe_penalty=safe,
        applied_penalty=penalty,
        safety_guaranteed=penalty > objective_upper,
        scale=config.penalty_scale,
        margin=config.penalty_margin,
    )
    builders = {
        name: PolynomialBuilder()
        for name in (
            "business",
            "coverage",
            "mandatory",
            "overlap",
            "workload",
            "imbalance_constraints",
        )
    }

    def integer(
        name: str, role: IntegerRole, bound: int, worker_id: str | None = None, unit: int = 1
    ) -> dict[int, int]:
        if bound.bit_length() > config.max_variables - len(variables):
            raise ValueError(f"encoding exceeds max_variables={config.max_variables}")
        kind: VariableKind = "slack" if role.endswith("slack") else "auxiliary"
        weights = bounded_weights(bound)
        indices = tuple(
            variable(f"{name}:bit-{position}", kind, group=name, weight=weight, worker_id=worker_id)
            for position, weight in enumerate(weights)
        )
        integers.append(
            IntegerEncoding(
                name=name,
                role=role,
                worker_id=worker_id,
                upper_bound=bound,
                unit=unit,
                indices=indices,
            )
        )
        return dict(zip(indices, weights, strict=True))

    def equation(name: str, terms: list[tuple[dict[int, int], int]], constant: int = 0) -> None:
        coefficients: dict[int, int] = {}
        for block, multiplier in terms:
            for index, coefficient in block.items():
                coefficients[index] = coefficients.get(index, 0) + multiplier * coefficient
        builders[name].add_square(coefficients, constant, penalty)

    for job_id, job in jobs.items():
        group = {index: 1 for (ji, _), index in assignment.items() if ji == job_id}
        group[omitted[job_id]] = 1
        builders["coverage"].add_square(group, -1, penalty)
        builders["business"].add_linear(omitted[job_id], unassigned_cost(job, config.objective))
        if job.must_assign:
            builders["mandatory"].add_linear(omitted[job_id], penalty)
    for (job_id, worker_id), index in assignment.items():
        builders["business"].add_linear(
            index, assignment_cost(workers[worker_id], jobs[job_id], config.objective)
        )

    for worker_id, worker in workers.items():
        assigned = [(jobs[ji], index) for (ji, wi), index in assignment.items() if wi == worker_id]
        for position, (left, li) in enumerate(assigned):
            for right, ri in assigned[position + 1 :]:
                if left.start_minute < right.end_minute and right.start_minute < left.end_minute:
                    builders["overlap"].add_pair(li, ri, penalty)
        load = raw_loads[worker_id]
        if sum(load.values()) > worker.max_work_minutes:
            unit = math.gcd(*load.values()) or 1
            capacity = worker.max_work_minutes // unit
            slack = integer(
                "capacity:" + json.dumps(worker_id), "workload_slack", capacity, worker_id, unit
            )
            equation(
                "workload", [({i: d // unit for i, d in load.items()}, 1), (slack, 1)], -capacity
            )

    if imbalance_active:
        maximum = integer("range:maximum", "load_max", horizon, unit=load_unit)
        minimum_bound = min(upper_loads.values())
        minimum = integer("range:minimum", "load_min", minimum_bound, unit=load_unit)
        difference = integer("range:difference", "load_range", horizon, unit=load_unit)
        for wi, load in normalized_loads.items():
            upper_slack = integer(
                "range:upper:" + json.dumps(wi), "upper_slack", horizon, wi, load_unit
            )
            equation("imbalance_constraints", [(maximum, 1), (load, -1), (upper_slack, -1)])
            if minimum_bound:
                lower_slack = integer(
                    "range:lower:" + json.dumps(wi), "lower_slack", upper_loads[wi], wi, load_unit
                )
                equation("imbalance_constraints", [(load, 1), (minimum, -1), (lower_slack, -1)])
        equation("imbalance_constraints", [(maximum, 1), (minimum, -1), (difference, -1)])
        for index, weight in difference.items():
            builders["business"].add_linear(
                index, config.objective.imbalance_weight * load_unit * weight
            )

    size = len(variables)
    mapping = VariableMapping(
        instance_id=instance.instance_id,
        instance_hash=instance_fingerprint(instance),
        variables=tuple(variables),
        integers=tuple(integers),
        original_possible_assignments=len(jobs) * len(workers),
        removed_assignments=tuple(removed),
        load_unit=load_unit,
    )
    components = {name: builder.freeze(size) for name, builder in builders.items()}
    total = PolynomialBuilder()
    for component in components.values():
        total.add_polynomial(component)
    return QUBOModel(
        **total.freeze(size).model_dump(),
        mapping=mapping,
        config=config,
        penalties=penalties,
        components=components,
    )
