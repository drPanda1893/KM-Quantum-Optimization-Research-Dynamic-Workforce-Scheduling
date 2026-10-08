"""Complete tiny-instance checks against both original classical references."""

from itertools import product
from time import perf_counter
from typing import Literal, Self

from pydantic import Field, model_validator

from km_quantum.domain import (
    Assignment,
    FrozenModel,
    Schedule,
    SchedulingInstance,
    SolverConfig,
    SolverResult,
)
from km_quantum.metrics import evaluate_objective, validate_schedule
from km_quantum.solvers import solve_cp_sat, solve_exhaustive

from .decoder import decode_bitstring, encode_schedule
from .encoder import encode_qubo
from .exhaustive import (
    DEFAULT_MAX_STATES,
    QUBOSolverResult,
    _PolynomialTracker,
    _state_count,
    solve_qubo,
)
from .ising import IsingModel, qubo_to_ising
from .models import Polynomial, QUBOConfig, QUBOModel, evaluate_qubo_energy


class IsingEquivalenceResult(FrozenModel):
    equivalent: bool
    state_count: int = Field(gt=0)
    checked_states: int = Field(gt=0)
    mismatch_count: int = Field(ge=0)
    maximum_absolute_difference4: int = Field(ge=0)
    mismatch_examples: tuple[str, ...]
    ising_model: IsingModel
    runtime_ms: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def complete_proof(self) -> Self:
        if (
            self.checked_states != self.state_count
            or self.state_count != 1 << self.ising_model.number_variables
        ):
            raise ValueError("Ising equivalence requires checking the full state space")
        if self.equivalent != (self.mismatch_count == 0) or self.equivalent != (
            self.maximum_absolute_difference4 == 0
        ):
            raise ValueError("Ising equivalence flag disagrees with mismatch diagnostics")
        return self


class VerificationResult(FrozenModel):
    status: Literal["PASS", "FAIL", "INFEASIBLE", "INCONCLUSIVE"]
    equivalence_passed: bool
    reasons: tuple[str, ...]
    original_feasible: bool | None
    classical_objective: int | None
    cp_sat_result: SolverResult | None
    exhaustive_result: SolverResult | None
    qubo_result: QUBOSolverResult
    ising_result: IsingEquivalenceResult
    canonical_encoding_checked_count: int = Field(ge=0)
    canonical_encoding_mismatch_count: int = Field(ge=0)
    canonical_encoding_complete: bool
    strict_invalid_margin: bool
    runtime_ms: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def honest_status(self) -> Self:
        if self.equivalence_passed != (self.status == "PASS"):
            raise ValueError("equivalence_passed must agree with PASS status")
        margin = self.qubo_result.invalid_energy_margin
        if self.strict_invalid_margin != (margin is None or margin > 0):
            raise ValueError("strict invalid margin flag disagrees with enumerated energies")
        if self.canonical_encoding_mismatch_count > self.canonical_encoding_checked_count:
            raise ValueError("canonical mismatch count exceeds checked schedules")
        if self.status == "PASS":
            if (
                self.reasons
                or self.original_feasible is not True
                or self.classical_objective is None
            ):
                raise ValueError(
                    "PASS requires a feasible original with an exact reference objective"
                )
            if (
                not self.ising_result.equivalent
                or not self.strict_invalid_margin
                or not self.canonical_encoding_complete
                or self.canonical_encoding_mismatch_count
            ):
                raise ValueError("PASS requires complete successful energy and canonical checks")
            for reference in (self.cp_sat_result, self.exhaustive_result):
                if (
                    reference is None
                    or reference.status != "OPTIMAL"
                    or reference.objective_value != self.classical_objective
                ):
                    raise ValueError("PASS requires both proven classical reference optima")
            if self.qubo_result.ground_energy != self.classical_objective or any(
                not ground.encoded_feasible
                or not ground.objective_matches
                or ground.business_objective != self.classical_objective
                for ground in self.qubo_result.ground_states
            ):
                raise ValueError("PASS requires every ground state to match the original optimum")
        return self


class _IsingTracker:
    def __init__(self, model: IsingModel) -> None:
        self.value = model.energy4((1,) * model.number_variables)
        self.fields = [0] * model.number_variables
        self.neighbors: list[list[tuple[int, int]]] = [[] for _ in range(model.number_variables)]
        for term in model.fields:
            self.fields[term.index] += term.coefficient
        for pair in model.couplings:
            self.neighbors[pair.left].append((pair.right, pair.coefficient))
            self.neighbors[pair.right].append((pair.left, pair.coefficient))

    def flip(self, index: int, bits: list[int]) -> None:
        spin = 1 - 2 * bits[index]
        field = self.fields[index] + sum(
            coefficient * (1 - 2 * bits[neighbor])
            for neighbor, coefficient in self.neighbors[index]
        )
        self.value -= 2 * spin * field


def verify_ising_equivalence(
    model: Polynomial, *, max_states: int = DEFAULT_MAX_STATES
) -> IsingEquivalenceResult:
    """Check 4*E_QUBO(x)=4*H_Ising(1-2x) at every state, including offsets."""
    started = perf_counter()
    count = _state_count(model.number_variables, max_states)
    ising = qubo_to_ising(model)
    qubo_tracker = _PolynomialTracker(model)
    ising_tracker = _IsingTracker(ising)
    bits = [0] * model.number_variables
    previous = mismatch_count = maximum_difference = 0
    examples: list[str] = []
    for index in range(count):
        gray = index ^ (index >> 1)
        if index:
            changed = (gray ^ previous).bit_length() - 1
            qubo_tracker.flip(changed, bits)
            ising_tracker.flip(changed, bits)
            bits[changed] ^= 1
        previous = gray
        difference = abs(4 * qubo_tracker.value - ising_tracker.value)
        if difference:
            mismatch_count += 1
            maximum_difference = max(maximum_difference, difference)
            if len(examples) < 8:
                examples.append("".join(map(str, bits)))
    return IsingEquivalenceResult(
        equivalent=mismatch_count == 0,
        state_count=count,
        checked_states=count,
        mismatch_count=mismatch_count,
        maximum_absolute_difference4=maximum_difference,
        mismatch_examples=tuple(examples),
        ising_model=ising,
        runtime_ms=(perf_counter() - started) * 1000,
    )


def _canonical_check(
    instance: SchedulingInstance, model: QUBOModel, max_states: int
) -> tuple[int, int, int | None]:
    """Enumerate original worker choices, independently of QUBO preprocessing."""
    count = 1
    for _ in instance.jobs:
        count *= len(instance.workers) + 1
        if count > max_states:
            raise ValueError("Original canonical-encoding enumeration exceeds max_states")
    checked = mismatches = 0
    best_objective = None
    for choices in product(range(len(instance.workers) + 1), repeat=len(instance.jobs)):
        schedule = Schedule(
            instance_id=instance.instance_id,
            assignments=tuple(
                Assignment(worker_id=instance.workers[choice - 1].id, job_id=job.id)
                for job, choice in zip(instance.jobs, choices, strict=True)
                if choice
            ),
            unassigned_job_ids=tuple(
                job.id for job, choice in zip(instance.jobs, choices, strict=True) if not choice
            ),
        )
        if not validate_schedule(instance, schedule).is_valid:
            continue
        checked += 1
        objective = evaluate_objective(instance, schedule, model.config.objective).total
        best_objective = objective if best_objective is None else min(best_objective, objective)
        try:
            bits = encode_schedule(instance, model.mapping, schedule)
            breakdown = evaluate_qubo_energy(model, bits)
            decoded = decode_bitstring(instance, model.mapping, bits)
            same_assignments = {(a.job_id, a.worker_id) for a in decoded.assignments} == {
                (a.job_id, a.worker_id) for a in schedule.assignments
            }
            same_unassigned = set(decoded.unassigned_job_ids) == set(schedule.unassigned_job_ids)
            matches = (
                breakdown.constraint_total == 0
                and breakdown.business == breakdown.total == objective
                and validate_schedule(instance, decoded).is_valid
                and same_assignments
                and same_unassigned
            )
        except (ValueError, KeyError):
            matches = False
        mismatches += not matches
    return checked, mismatches, best_objective


def verify_equivalence(
    instance: SchedulingInstance,
    config: QUBOConfig | None = None,
    *,
    model: QUBOModel | None = None,
    max_states: int = DEFAULT_MAX_STATES,
    analyze_spectrum: bool = True,
) -> VerificationResult:
    """Certify equivalence only after all states and both references finish.

    Original infeasibility is a separate outcome, never a fabricated PASS.
    Weak penalties may pass on an individual completely enumerated instance;
    that empirical result does not create a general penalty safety guarantee.
    """
    started = perf_counter()
    if model is None:
        model = encode_qubo(instance, config)
    else:
        if config is not None and config != model.config:
            raise ValueError("Verification config differs from the supplied QUBO model config")
        if model != encode_qubo(instance, model.config):
            raise ValueError("Supplied QUBO model differs from the canonical encoding")
    qubo = solve_qubo(model, instance, max_states=max_states, analyze_spectrum=analyze_spectrum)
    ising = verify_ising_equivalence(model, max_states=max_states)
    solver_config = SolverConfig(
        objective=model.config.objective,
        time_limit_seconds=60.0,
        exhaustive_max_states=max_states,
    )
    limitations: list[str] = []
    cp = exhaustive = None
    try:
        cp = solve_cp_sat(instance, solver_config)
    except ValueError as exc:
        limitations.append(f"CP-SAT reference unavailable: {exc}")
    try:
        exhaustive = solve_exhaustive(instance, solver_config)
    except ValueError as exc:
        limitations.append(f"Exhaustive reference unavailable: {exc}")
    canonical_checked = canonical_mismatches = 0
    canonical_complete = False
    canonical_best = None
    try:
        canonical_checked, canonical_mismatches, canonical_best = _canonical_check(
            instance, model, max_states
        )
        canonical_complete = True
    except ValueError as exc:
        limitations.append(str(exc))

    failures: list[str] = []
    if not ising.equivalent:
        failures.append("QUBO and Ising energies differ")
    if canonical_mismatches:
        failures.append(f"{canonical_mismatches} canonical feasible encodings differ from M1")
    references_proven = (
        cp is not None
        and exhaustive is not None
        and (
            cp.status in ("OPTIMAL", "INFEASIBLE")
            and exhaustive.status in ("OPTIMAL", "INFEASIBLE")
        )
    )
    if not references_proven:
        limitations.append("Both original references must finish with proof")
    original_feasible = None
    classical_objective = None
    status: Literal["PASS", "FAIL", "INFEASIBLE", "INCONCLUSIVE"] = "INCONCLUSIVE"
    margin_strict = qubo.invalid_energy_margin is None or qubo.invalid_energy_margin > 0
    if references_proven:
        assert cp is not None and exhaustive is not None
        if cp.status != exhaustive.status or cp.objective_value != exhaustive.objective_value:
            failures.append("CP-SAT and original exhaustive reference disagree")
        elif cp.status == "INFEASIBLE":
            original_feasible = False
            if qubo.encoding_valid_state_count or canonical_checked:
                failures.append(
                    "QUBO contains a valid encoding of an allegedly infeasible original"
                )
            else:
                status = "INFEASIBLE"
        else:
            original_feasible = True
            classical_objective = cp.objective_value
            if canonical_complete and canonical_best != classical_objective:
                failures.append("Canonical original enumeration disagrees with reference optimum")
            if qubo.ground_energy != classical_objective:
                failures.append("QUBO ground energy differs from the original optimum")
            if any(not ground.encoded_feasible for ground in qubo.ground_states):
                failures.append("At least one QUBO ground state violates hard constraints")
            if any(
                ground.business_objective != classical_objective or not ground.objective_matches
                for ground in qubo.ground_states
            ):
                failures.append("At least one ground state has a different M1 business objective")
            if not margin_strict:
                failures.append("Invalid states are not strictly above the QUBO ground energy")
            if not failures and not limitations:
                status = "PASS"
    if failures:
        status = "FAIL"
    elif limitations:
        status = "INCONCLUSIVE"
    reasons = tuple(failures + limitations)
    if status == "INFEASIBLE":
        reasons = ("Both original references prove infeasibility; QUBO minima retain violations",)
    return VerificationResult(
        status=status,
        equivalence_passed=status == "PASS",
        reasons=reasons,
        original_feasible=original_feasible,
        classical_objective=classical_objective,
        cp_sat_result=cp,
        exhaustive_result=exhaustive,
        qubo_result=qubo,
        ising_result=ising,
        canonical_encoding_checked_count=canonical_checked,
        canonical_encoding_mismatch_count=canonical_mismatches,
        canonical_encoding_complete=canonical_complete,
        strict_invalid_margin=margin_strict,
        runtime_ms=(perf_counter() - started) * 1000,
    )
