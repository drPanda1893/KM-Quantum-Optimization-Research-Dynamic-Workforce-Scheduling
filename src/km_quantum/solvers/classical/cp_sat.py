"""OR-Tools CP-SAT baseline with independent result verification.

Every compatible worker/job pair has a Boolean assignment variable and an
optional fixed interval. An explicit unassigned variable completes each job's
coverage equation, including jobs with no compatible worker.
"""

from importlib.metadata import version
from time import perf_counter
from typing import Any, Literal, cast

from ortools.sat.python import cp_model

from km_quantum.domain import Assignment, Schedule, SchedulingInstance, SolverConfig, SolverResult
from km_quantum.metrics import (
    assignment_cost,
    evaluate_objective,
    unassigned_cost,
    validate_schedule,
)

# OR-Tools uses signed int64 arithmetic internally, while its public objective
# and bound accessors return doubles. A conservative aggregate cap protects
# both integer arithmetic and exact integer comparisons through those accessors.
MAX_EXACT_INTEGER = (1 << 53) - 1


def _check_numeric_range(values: list[int], description: str) -> None:
    if any(value < 0 or value > MAX_EXACT_INTEGER for value in values):
        raise ValueError(f"{description} must be between 0 and {MAX_EXACT_INTEGER}")


def solve(instance: SchedulingInstance, config: SolverConfig | None = None) -> SolverResult:
    """Minimize the centrally defined integer objective for a fixed-slot instance.

    Returned incumbents are validated and rescored outside CP-SAT. A status of
    OPTIMAL is the only status that sets ``proven_optimal``. UNKNOWN, INFEASIBLE
    and MODEL_INVALID never carry a schedule.
    """
    started = perf_counter()
    config = config or SolverConfig()
    model = cp_model.CpModel()
    assignments: dict[tuple[int, int], cp_model.IntVar] = {}
    unassigned: list[cp_model.IntVar] = []
    worker_intervals: list[list[cp_model.IntervalVar]] = [[] for _ in instance.workers]
    worker_terms: list[list[Any]] = [[] for _ in instance.workers]
    objective_terms: list[Any] = []
    objective_coefficients: list[int] = []

    _check_numeric_range(
        [value for job in instance.jobs for value in (job.start_minute, job.end_minute)],
        "Job times",
    )
    total_duration = sum(job.duration_minutes for job in instance.jobs)
    _check_numeric_range([total_duration], "Total job duration")

    for job_index, job in enumerate(instance.jobs):
        job_assignments: list[cp_model.IntVar] = []
        for worker_index, worker in enumerate(instance.workers):
            compatible = job.required_skills <= worker.skills and any(
                window.start_minute <= job.start_minute and job.end_minute <= window.end_minute
                for window in worker.availability
            )
            if not compatible:
                continue
            selected = model.new_bool_var(f"assign_j{job_index}_w{worker_index}")
            assignments[worker_index, job_index] = selected
            job_assignments.append(selected)
            worker_intervals[worker_index].append(
                model.new_optional_fixed_size_interval_var(
                    job.start_minute,
                    job.duration_minutes,
                    selected,
                    f"interval_j{job_index}_w{worker_index}",
                )
            )
            worker_terms[worker_index].append(job.duration_minutes * selected)
            coefficient = assignment_cost(worker, job, config.objective)
            _check_numeric_range([coefficient], "Objective coefficients")
            objective_coefficients.append(coefficient)
            if coefficient:
                objective_terms.append(coefficient * selected)

        skipped = model.new_bool_var(f"unassigned_j{job_index}")
        unassigned.append(skipped)
        model.add(sum(job_assignments) + skipped == 1)
        if job.must_assign:
            model.add(skipped == 0)
        coefficient = unassigned_cost(job, config.objective)
        _check_numeric_range([coefficient], "Objective coefficients")
        objective_coefficients.append(coefficient)
        if coefficient:
            objective_terms.append(coefficient * skipped)

    loads: list[cp_model.IntVar] = []
    load_bounds: list[int] = []
    for index, worker in enumerate(instance.workers):
        # Do not put unnecessarily large capacities into the CP domain.
        load_bound = min(worker.max_work_minutes, total_duration)
        load_bounds.append(load_bound)
        load = model.new_int_var(0, load_bound, f"load_w{index}")
        model.add(load == sum(worker_terms[index]))
        model.add_no_overlap(worker_intervals[index])
        loads.append(load)

    if config.objective.imbalance_weight and len(loads) >= 2:
        _check_numeric_range([config.objective.imbalance_weight], "Imbalance weight")
        maximum_load_bound = max(load_bounds)
        maximum_load = model.new_int_var(0, maximum_load_bound, "maximum_load")
        minimum_load = model.new_int_var(0, maximum_load_bound, "minimum_load")
        imbalance = model.new_int_var(0, maximum_load_bound, "imbalance")
        model.add_max_equality(maximum_load, loads)
        model.add_min_equality(minimum_load, loads)
        model.add(imbalance == maximum_load - minimum_load)
        objective_terms.append(config.objective.imbalance_weight * imbalance)
        objective_coefficients.append(config.objective.imbalance_weight * maximum_load_bound)

    # CP's domain-based overflow validation does not rely on job coverage to
    # tighten the objective. Check the expanded coefficient sum accordingly.
    _check_numeric_range(objective_coefficients, "Objective coefficients")
    _check_numeric_range([sum(objective_coefficients)], "Expanded objective upper bound")
    objective_expression = sum(objective_terms)
    model.minimize(objective_expression)
    model_error = model.validate()
    metadata: dict[str, Any] = {
        "solver": "cp_sat",
        "solver_version": version("ortools"),
        "config": config.model_dump(mode="json"),
        "assignment_variable_count": len(assignments),
        "unassigned_variable_count": len(unassigned),
        "model_build_ms": (perf_counter() - started) * 1000,
    }
    if model_error:
        metadata["model_validation_error"] = model_error
        return SolverResult(
            status="MODEL_INVALID",
            schedule=None,
            objective_value=None,
            runtime_ms=(perf_counter() - started) * 1000,
            proven_optimal=False,
            best_bound=None,
            optimality_gap=None,
            solver_metadata=metadata,
        )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = config.time_limit_seconds
    solver.parameters.num_search_workers = config.num_search_workers
    solver.parameters.random_seed = config.random_seed
    if config.max_deterministic_time is not None:
        solver.parameters.max_deterministic_time = config.max_deterministic_time
    status_code = solver.solve(model)
    status = cast(
        Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN", "MODEL_INVALID"],
        solver.status_name(status_code),
    )
    metadata.update(
        {
            "search_wall_time_ms": solver.wall_time * 1000,
            "deterministic_time": solver.response_proto.deterministic_time,
            "branches": solver.num_branches,
            "conflicts": solver.num_conflicts,
        }
    )

    schedule = None
    objective = None
    gap = None
    bound = None
    if status_code in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        candidate = Schedule(
            instance_id=instance.instance_id,
            assignments=tuple(
                Assignment(worker_id=instance.workers[wi].id, job_id=instance.jobs[ji].id)
                for (wi, ji), variable in assignments.items()
                if solver.value(variable)
            ),
            unassigned_job_ids=tuple(
                job.id
                for job, variable in zip(instance.jobs, unassigned, strict=True)
                if solver.value(variable)
            ),
        )
        validation = validate_schedule(instance, candidate)
        if not validation.is_valid:
            raise RuntimeError(f"CP-SAT returned an invalid schedule: {validation.violations}")
        objective = evaluate_objective(instance, candidate, config.objective).total
        integer_objective = solver.value(objective_expression)
        if objective != integer_objective:
            raise RuntimeError(
                "CP-SAT objective does not match the independent objective evaluator"
            )
        raw_bound = solver.best_objective_bound
        metadata["integer_objective_value"] = integer_objective
        metadata["raw_objective_value"] = solver.objective_value
        metadata["raw_best_objective_bound"] = raw_bound
        if raw_bound > objective + 1e-6:
            raise RuntimeError("CP-SAT lower bound exceeds independently evaluated objective")
        # Objective scaling can produce tiny floating-point differences in the
        # public bound even for a proven integer optimum. Retain the raw bound
        # for audit and report the exact independently scored optimum on proof.
        bound = float(objective) if status_code == cp_model.OPTIMAL else min(raw_bound, objective)
        gap = max(0.0, (objective - bound) / max(1, abs(objective)))
        schedule = candidate.model_copy(
            update={"objective_value": objective, "solver_metadata": metadata.copy()}
        )
    elif status_code == cp_model.UNKNOWN:
        bound = solver.best_objective_bound

    return SolverResult(
        status=status,
        schedule=schedule,
        objective_value=objective,
        runtime_ms=(perf_counter() - started) * 1000,
        proven_optimal=status_code == cp_model.OPTIMAL,
        best_bound=bound,
        optimality_gap=gap,
        solver_metadata=metadata,
    )
