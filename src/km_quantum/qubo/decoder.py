"""Honest projection and canonical lifting; projection never repairs a bitstring."""

from collections.abc import Sequence

from km_quantum.domain import Assignment, Schedule, SchedulingInstance
from km_quantum.metrics import validate_schedule

from .encoder import eligible_pairs, instance_fingerprint
from .models import VariableMapping, normalize_bits


def validate_mapping(instance: SchedulingInstance, mapping: VariableMapping) -> None:
    if mapping.instance_id != instance.instance_id or mapping.instance_hash != instance_fingerprint(
        instance
    ):
        raise ValueError("mapping belongs to a different instance")
    pairs, removed = eligible_pairs(instance)
    actual = {(v.job_id, v.worker_id) for v in mapping.variables if v.kind == "assignment"}
    omitted = {v.job_id for v in mapping.variables if v.kind == "unassigned"}
    if actual != set(pairs) or omitted != {job.id for job in instance.jobs}:
        raise ValueError("mapping is inconsistent with deterministic preprocessing")
    if mapping.removed_assignments != tuple(removed):
        raise ValueError("mapping pruning audit does not match the instance")


def auxiliary_values(mapping: VariableMapping, bits: str | Sequence[int]) -> dict[str, int]:
    state = normalize_bits(bits, len(mapping.variables))
    return {
        group.name: sum(mapping.variables[i].weight * state[i] for i in group.indices)
        for group in mapping.integers
    }


def decode_bitstring(
    instance: SchedulingInstance, mapping: VariableMapping, bitstring: str | Sequence[int]
) -> Schedule:
    validate_mapping(instance, mapping)
    state = normalize_bits(bitstring, len(mapping.variables))
    assignments: list[Assignment] = []
    omitted: list[str] = []
    for variable in mapping.variables:
        if not state[variable.index]:
            continue
        if variable.kind == "assignment":
            assert variable.job_id is not None and variable.worker_id is not None
            assignments.append(Assignment(job_id=variable.job_id, worker_id=variable.worker_id))
        elif variable.kind == "unassigned":
            assert variable.job_id is not None
            omitted.append(variable.job_id)
    return Schedule(
        instance_id=instance.instance_id,
        assignments=tuple(assignments),
        unassigned_job_ids=tuple(omitted),
        solver_metadata={
            "source": "qubo_decoder",
            "repair_applied": False,
        },
    )


def encode_schedule(
    instance: SchedulingInstance, mapping: VariableMapping, schedule: Schedule
) -> tuple[int, ...]:
    """Lift a feasible M1 schedule to exact slack values and exact extrema."""
    validate_mapping(instance, mapping)
    validation = validate_schedule(instance, schedule)
    if not validation.is_valid:
        raise ValueError(f"cannot encode invalid schedule: {validation.violations}")
    jobs = {job.id: job for job in instance.jobs}
    selected = {(assignment.job_id, assignment.worker_id) for assignment in schedule.assignments}
    loads = dict.fromkeys((worker.id for worker in instance.workers), 0)
    for ji, wi in selected:
        loads[wi] += jobs[ji].duration_minutes
    maximum = max(loads.values(), default=0)
    minimum = min(loads.values(), default=0)
    state = [0] * len(mapping.variables)
    for variable in mapping.variables:
        if variable.kind == "assignment":
            state[variable.index] = int((variable.job_id, variable.worker_id) in selected)
        elif variable.kind == "unassigned":
            state[variable.index] = int(variable.job_id in schedule.unassigned_job_ids)
    for group in mapping.integers:
        load = loads[group.worker_id] if group.worker_id is not None else 0
        if group.role == "workload_slack":
            value = group.upper_bound - load // group.unit
        elif group.role == "load_max":
            value = maximum // group.unit
        elif group.role == "load_min":
            value = minimum // group.unit
        elif group.role == "load_range":
            value = (maximum - minimum) // group.unit
        elif group.role == "upper_slack":
            value = (maximum - load) // group.unit
        else:
            value = (load - minimum) // group.unit
        if value < 0 or value > group.upper_bound:
            raise ValueError("schedule cannot be represented by this integer encoding")
        # Descending weight greedy works because bounded weights cover the whole
        # contiguous range. Stable index breaks ties between duplicate weights.
        for index in sorted(group.indices, key=lambda i: (-mapping.variables[i].weight, i)):
            weight = mapping.variables[index].weight
            if value >= weight:
                state[index] = 1
                value -= weight
        if value:
            raise ValueError("bounded integer encoding failed to represent a canonical value")
    return tuple(state)
