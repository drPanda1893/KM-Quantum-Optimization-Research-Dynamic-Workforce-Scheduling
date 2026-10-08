"""Complete, capped QUBO enumeration with every ground state retained."""

from collections import Counter
from collections.abc import Iterator
from time import perf_counter
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from km_quantum.domain import FrozenModel, Schedule, SchedulingInstance
from km_quantum.metrics import ValidationResult, evaluate_objective, validate_schedule

from .decoder import decode_bitstring
from .encoder import instance_fingerprint
from .models import EnergyBreakdown, Polynomial, QUBOModel, evaluate_qubo_energy

DEFAULT_MAX_STATES = 1_048_576
CONSTRAINT_COMPONENTS = ("coverage", "mandatory", "overlap", "workload", "imbalance_constraints")


class EnergyLevel(FrozenModel):
    energy: int
    degeneracy: int = Field(gt=0)


class GroundState(FrozenModel):
    bits: str
    energy: int
    breakdown: EnergyBreakdown
    schedule: Schedule
    validation: ValidationResult
    business_objective: int | None
    encoded_feasible: bool
    objective_matches: bool

    @field_validator("validation", mode="before")
    @classmethod
    def restore_validation(cls, value: object) -> object:
        # M1's computed count is serialized for diagnostics but its input schema
        # forbids that extra field. Verify it before dropping only that field.
        if isinstance(value, dict) and "violation_count" in value:
            violations = value.get("violations", ())
            if not isinstance(violations, (list, tuple)):
                raise ValueError("serialized validation violations must be a sequence")
            if type(value["violation_count"]) is not int or value["violation_count"] != len(
                violations
            ):
                raise ValueError("serialized validation violation_count is inconsistent")
            return {key: item for key, item in value.items() if key != "violation_count"}
        return value

    @model_validator(mode="after")
    def coherent_ground(self) -> Self:
        if any(bit not in "01" for bit in self.bits) or self.energy != self.breakdown.total:
            raise ValueError("ground bitstring or reported energy is inconsistent")
        if self.encoded_feasible != (
            self.validation.is_valid and self.breakdown.constraint_total == 0
        ):
            raise ValueError("encoded feasibility differs from validation and penalties")
        if self.validation.is_valid != (self.business_objective is not None):
            raise ValueError("only valid schedules have a business objective")
        if self.schedule.objective_value != self.business_objective:
            raise ValueError("schedule and independently evaluated objective differ")
        if self.objective_matches != (
            self.validation.is_valid and self.breakdown.business == self.business_objective
        ):
            raise ValueError("objective_matches differs from the reported business energies")
        return self


class QUBOSolverResult(FrozenModel):
    """Proof concerns the binary polynomial, not original scheduling feasibility."""

    ground_energy: int
    ground_states: tuple[GroundState, ...]
    ground_state_degeneracy: int = Field(gt=0)
    first_excited_energy: int | None
    first_excited_gap: int | None
    spectrum: tuple[EnergyLevel, ...]
    spectrum_analyzed: bool
    state_count: int = Field(gt=0)
    explored_states: int = Field(gt=0)
    enumeration_complete: Literal[True] = True
    proven_ground_state: Literal[True] = True
    encoding_valid_state_count: int = Field(ge=0)
    encoding_invalid_state_count: int = Field(ge=0)
    constraint_zero_state_count: int = Field(ge=0)
    schedule_valid_state_count: int = Field(ge=0)
    schedule_invalid_state_count: int = Field(ge=0)
    schedule_valid_auxiliary_invalid_state_count: int = Field(ge=0)
    schedule_valid_noncanonical_state_count: int = Field(ge=0)
    unique_primary_state_count: int = Field(gt=0)
    best_valid_energy: int | None
    best_invalid_energy: int | None
    invalid_energy_margin: int | None
    runtime_ms: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def coherent_counts(self) -> Self:
        bits = [ground.bits for ground in self.ground_states]
        if not bits or bits != sorted(set(bits)) or len(bits) != self.ground_state_degeneracy:
            raise ValueError("all ground bitstrings must be present once in sorted order")
        if self.explored_states != self.state_count or self.state_count != 1 << len(bits[0]):
            raise ValueError("full state count and bitstring dimension disagree")
        if any(
            len(ground.bits) != len(bits[0]) or ground.energy != self.ground_energy
            for ground in self.ground_states
        ):
            raise ValueError("ground energies or dimensions disagree")
        if self.encoding_valid_state_count + self.encoding_invalid_state_count != self.state_count:
            raise ValueError("encoding validity counts must partition all states")
        if self.schedule_valid_state_count + self.schedule_invalid_state_count != self.state_count:
            raise ValueError("schedule validity counts must partition all states")
        if (
            self.schedule_valid_state_count
            != self.encoding_valid_state_count + self.schedule_valid_auxiliary_invalid_state_count
        ):
            raise ValueError(
                "schedule-valid states must partition valid and auxiliary-invalid encodings"
            )
        if (self.best_valid_energy is None) != (self.encoding_valid_state_count == 0):
            raise ValueError("best valid energy must agree with encoding validity counts")
        if self.best_valid_energy is not None and self.best_valid_energy < self.ground_energy:
            raise ValueError("best valid energy cannot be below the global ground energy")
        if self.first_excited_energy is None:
            if self.first_excited_gap is not None:
                raise ValueError("a gap requires an excited energy")
        elif (
            self.first_excited_gap != self.first_excited_energy - self.ground_energy
            or self.first_excited_gap <= 0
        ):
            raise ValueError("first excited gap must be a positive difference from the ground")
        expected_margin = (
            None
            if self.best_invalid_energy is None
            else self.best_invalid_energy - self.ground_energy
        )
        if self.invalid_energy_margin != expected_margin or (
            expected_margin is not None and expected_margin < 0
        ):
            raise ValueError("invalid margin differs from the reported energies")
        if self.spectrum_analyzed:
            levels = [level.energy for level in self.spectrum]
            if (
                levels != sorted(set(levels))
                or sum(level.degeneracy for level in self.spectrum) != self.state_count
            ):
                raise ValueError("complete spectrum levels and degeneracies are inconsistent")
            if (
                not self.spectrum
                or self.spectrum[0].energy != self.ground_energy
                or self.spectrum[0].degeneracy != self.ground_state_degeneracy
            ):
                raise ValueError("spectrum and ground state degeneracy disagree")
        elif self.spectrum:
            raise ValueError("unanalyzed spectra must be omitted")
        return self


def _state_count(number_variables: int, max_states: int) -> int:
    if isinstance(max_states, bool) or not isinstance(max_states, int) or max_states < 1:
        raise ValueError("max_states must be a positive integer")
    if number_variables >= max_states.bit_length():
        raise ValueError(f"2**{number_variables} QUBO states exceed max_states={max_states}")
    return 1 << number_variables


class _PolynomialTracker:
    """Exact local-field update on one Gray-code bit flip."""

    def __init__(self, polynomial: Polynomial) -> None:
        self.value = polynomial.constant
        self.linear = [0] * polynomial.number_variables
        self.neighbors: list[list[tuple[int, int]]] = [
            [] for _ in range(polynomial.number_variables)
        ]
        for term in polynomial.linear:
            self.linear[term.index] += term.coefficient
        for pair in polynomial.quadratic:
            self.neighbors[pair.left].append((pair.right, pair.coefficient))
            self.neighbors[pair.right].append((pair.left, pair.coefficient))

    def flip(self, index: int, bits: list[int]) -> None:
        direction = 1 - 2 * bits[index]
        local_field = self.linear[index] + sum(
            coefficient * bits[neighbor] for neighbor, coefficient in self.neighbors[index]
        )
        self.value += direction * local_field


def _energy_states(
    model: QUBOModel, max_states: int
) -> Iterator[tuple[int, list[int], int, dict[str, int]]]:
    """Yield transient bits/components for all states in deterministic Gray order."""
    states = _state_count(model.number_variables, max_states)
    expected_components = {"business", *CONSTRAINT_COMPONENTS}
    if set(model.components) != expected_components:
        raise ValueError("QUBO components must contain business and all five constraint groups")
    total = _PolynomialTracker(model)
    components = {name: _PolynomialTracker(poly) for name, poly in model.components.items()}
    bits = [0] * model.number_variables
    previous = 0
    for index in range(states):
        gray = index ^ (index >> 1)
        if index:
            changed = (gray ^ previous).bit_length() - 1
            total.flip(changed, bits)
            for tracker in components.values():
                tracker.flip(changed, bits)
            bits[changed] ^= 1
        previous = gray
        values = {name: tracker.value for name, tracker in components.items()}
        if total.value != sum(values.values()):
            raise ValueError("QUBO total polynomial does not equal its component polynomials")
        if any(values[name] < 0 for name in CONSTRAINT_COMPONENTS):
            raise ValueError("A constraint component has negative energy")
        yield gray, bits, total.value, values


def solve_qubo(
    model: QUBOModel,
    instance: SchedulingInstance,
    *,
    max_states: int = DEFAULT_MAX_STATES,
    analyze_spectrum: bool = True,
) -> QUBOSolverResult:
    """Enumerate every bitstring, never returning a truncated optimum or proof.

    Auxiliary violations make an encoding invalid even when its decoded schedule
    is independently valid. Noncanonical zero-penalty extrema remain valid
    epigraph encodings and are separately counted when their business energy is
    greater than the independently evaluated scheduling objective.
    """
    started = perf_counter()
    state_count = _state_count(model.number_variables, max_states)
    if model.mapping.instance_id != instance.instance_id or (
        model.mapping.instance_hash != instance_fingerprint(instance)
    ):
        raise ValueError("QUBO mapping does not match the supplied instance")
    primary_indices = [
        variable.index
        for variable in model.mapping.variables
        if variable.kind in ("assignment", "unassigned")
    ]
    if primary_indices != list(range(len(primary_indices))):
        raise ValueError("Primary QUBO variables must precede all auxiliary variables")
    primary_mask = (1 << len(primary_indices)) - 1
    cache: dict[int, tuple[Schedule, ValidationResult, int | None]] = {}
    levels: Counter[int] = Counter()
    ground_energy: int | None = None
    second_energy: int | None = None
    grounds: list[GroundState] = []
    encoding_valid = constraint_zero = schedule_valid = auxiliary_invalid = noncanonical = 0
    best_valid: int | None = None
    best_invalid: int | None = None

    for gray, bits, energy, components in _energy_states(model, max_states):
        primary = gray & primary_mask
        if primary not in cache:
            schedule = decode_bitstring(instance, model.mapping, bits)
            validation = validate_schedule(instance, schedule)
            objective = (
                evaluate_objective(instance, schedule, model.config.objective).total
                if validation.is_valid
                else None
            )
            if objective is not None:
                schedule = schedule.model_copy(update={"objective_value": objective})
            cache[primary] = schedule, validation, objective
        schedule, validation, objective = cache[primary]
        penalty = sum(components[name] for name in CONSTRAINT_COMPONENTS)
        zero_penalty = penalty == 0
        valid = zero_penalty and validation.is_valid
        objective_matches = validation.is_valid and components["business"] == objective
        constraint_zero += zero_penalty
        encoding_valid += valid
        schedule_valid += validation.is_valid
        auxiliary_invalid += validation.is_valid and not zero_penalty
        noncanonical += validation.is_valid and zero_penalty and not objective_matches
        if valid and (best_valid is None or energy < best_valid):
            best_valid = energy
        if not valid and (best_invalid is None or energy < best_invalid):
            best_invalid = energy
        if analyze_spectrum:
            levels[energy] += 1
        if ground_energy is None or energy < ground_energy:
            second_energy = ground_energy
            ground_energy = energy
            grounds.clear()
        elif energy > ground_energy and (second_energy is None or energy < second_energy):
            second_energy = energy
        if energy == ground_energy:
            grounds.append(
                GroundState(
                    bits="".join(map(str, bits)),
                    energy=energy,
                    breakdown=evaluate_qubo_energy(model, bits),
                    schedule=schedule,
                    validation=validation,
                    business_objective=objective,
                    encoded_feasible=valid,
                    objective_matches=objective_matches,
                )
            )
    assert ground_energy is not None
    grounds.sort(key=lambda ground: ground.bits)
    return QUBOSolverResult(
        ground_energy=ground_energy,
        ground_states=tuple(grounds),
        ground_state_degeneracy=len(grounds),
        first_excited_energy=second_energy,
        first_excited_gap=None if second_energy is None else second_energy - ground_energy,
        spectrum=tuple(EnergyLevel(energy=e, degeneracy=d) for e, d in sorted(levels.items())),
        spectrum_analyzed=analyze_spectrum,
        state_count=state_count,
        explored_states=state_count,
        encoding_valid_state_count=encoding_valid,
        encoding_invalid_state_count=state_count - encoding_valid,
        constraint_zero_state_count=constraint_zero,
        schedule_valid_state_count=schedule_valid,
        schedule_invalid_state_count=state_count - schedule_valid,
        schedule_valid_auxiliary_invalid_state_count=auxiliary_invalid,
        schedule_valid_noncanonical_state_count=noncanonical,
        unique_primary_state_count=len(cache),
        best_valid_energy=best_valid,
        best_invalid_energy=best_invalid,
        invalid_energy_margin=None if best_invalid is None else best_invalid - ground_energy,
        runtime_ms=(perf_counter() - started) * 1000,
    )
