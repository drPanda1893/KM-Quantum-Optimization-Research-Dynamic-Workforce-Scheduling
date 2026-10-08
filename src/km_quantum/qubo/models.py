"""Canonical exact integer polynomials, mappings and energy diagnostics."""

from collections.abc import Sequence
from fractions import Fraction
from typing import Any, Literal, Self

from pydantic import Field, computed_field, field_validator, model_validator

from km_quantum.domain import FrozenModel, ObjectiveWeights

Nonnegative = Field(ge=0, strict=True)
type VariableKind = Literal["assignment", "unassigned", "slack", "auxiliary"]
type IntegerRole = Literal[
    "workload_slack", "load_max", "load_min", "load_range", "upper_slack", "lower_slack"
]
type PruningReason = Literal["skill", "availability", "individual_capacity"]


def normalize_bits(bits: str | Sequence[int], size: int) -> tuple[int, ...]:
    """Index zero is the LEFTMOST character; no SDK endian convention is implied."""
    if isinstance(bits, str):
        if any(bit not in "01" for bit in bits):
            raise ValueError("bitstrings contain only '0' and '1'")
        result = tuple(int(bit) for bit in bits)
    else:
        if any(type(bit) is not int or bit not in (0, 1) for bit in bits):
            raise ValueError("bits must be integer zeros and ones")
        result = tuple(bits)
    if len(result) != size:
        raise ValueError(f"expected {size} bits, received {len(result)}")
    return result


class LinearTerm(FrozenModel):
    index: int = Nonnegative
    coefficient: int = Field(strict=True)


class QuadraticTerm(FrozenModel):
    left: int = Nonnegative
    right: int = Nonnegative
    coefficient: int = Field(strict=True)


class Polynomial(FrozenModel):
    number_variables: int = Nonnegative
    linear: tuple[LinearTerm, ...] = ()
    quadratic: tuple[QuadraticTerm, ...] = ()
    constant: int = Field(default=0, strict=True)

    @model_validator(mode="after")
    def canonical_terms(self) -> Self:
        indices = [term.index for term in self.linear]
        pairs = [(term.left, term.right) for term in self.quadratic]
        if indices != sorted(set(indices)) or pairs != sorted(set(pairs)):
            raise ValueError("terms must be unique and sorted")
        if any(
            term.index >= self.number_variables or term.coefficient == 0 for term in self.linear
        ):
            raise ValueError("linear terms must be nonzero with in-range indices")
        if any(
            not (term.left < term.right < self.number_variables) or term.coefficient == 0
            for term in self.quadratic
        ):
            raise ValueError("quadratic terms need nonzero coefficients and 0 <= left < right < n")
        return self

    def energy(self, bits: str | Sequence[int]) -> int:
        state = normalize_bits(bits, self.number_variables)
        return (
            self.constant
            + sum(term.coefficient * state[term.index] for term in self.linear)
            + sum(
                term.coefficient * state[term.left] * state[term.right] for term in self.quadratic
            )
        )

    def to_matrix(self) -> list[list[int]]:
        """Upper-triangular Q, with Q_ii=a_i and Q_ij=b_ij (i<j)."""
        result = [[0] * self.number_variables for _ in range(self.number_variables)]
        for term in self.linear:
            result[term.index][term.index] = term.coefficient
        for quadratic_term in self.quadratic:
            result[quadratic_term.left][quadratic_term.right] = quadratic_term.coefficient
        return result


class BinaryVariable(FrozenModel):
    index: int = Nonnegative
    name: str = Field(min_length=1)
    kind: VariableKind
    job_id: str | None = None
    worker_id: str | None = None
    group: str | None = None
    weight: int = Field(default=1, gt=0, strict=True)


class IntegerEncoding(FrozenModel):
    name: str
    role: IntegerRole
    worker_id: str | None = None
    upper_bound: int = Nonnegative
    unit: int = Field(default=1, gt=0, strict=True)
    indices: tuple[int, ...]


class PrunedAssignment(FrozenModel):
    job_id: str
    worker_id: str
    reason: PruningReason


class VariableMapping(FrozenModel):
    instance_id: str
    instance_hash: str
    variables: tuple[BinaryVariable, ...] = ()
    integers: tuple[IntegerEncoding, ...] = ()
    original_possible_assignments: int = Nonnegative
    removed_assignments: tuple[PrunedAssignment, ...] = ()
    load_unit: int = Field(default=1, gt=0, strict=True)

    @model_validator(mode="after")
    def coherent_mapping(self) -> Self:
        if [variable.index for variable in self.variables] != list(range(len(self.variables))):
            raise ValueError("mapping indices must be contiguous in serialization order")
        if len({variable.name for variable in self.variables}) != len(self.variables):
            raise ValueError("variable names must be unique")
        groups = {group.name: group for group in self.integers}
        if len(groups) != len(self.integers):
            raise ValueError("integer encoding names must be unique")
        auxiliary_indices: set[int] = set()
        for group in self.integers:
            weights = []
            for index in group.indices:
                if index < 0 or index >= len(self.variables) or index in auxiliary_indices:
                    raise ValueError("integer group indices must be unique and in range")
                variable = self.variables[index]
                if variable.group != group.name or variable.kind not in ("slack", "auxiliary"):
                    raise ValueError("integer group and variable annotations disagree")
                auxiliary_indices.add(index)
                weights.append(variable.weight)
            if sum(weights) != group.upper_bound:
                raise ValueError("bounded integer weights must sum to their upper bound")
            reachable = 0
            for weight in sorted(weights):
                if weight > reachable + 1:
                    raise ValueError("integer weights must represent every value in the range")
                reachable += weight
        seen_pairs: set[tuple[str | None, str | None]] = set()
        seen_jobs: set[str | None] = set()
        saw_auxiliary = False
        for variable in self.variables:
            if variable.kind in ("assignment", "unassigned"):
                if saw_auxiliary or variable.group is not None or variable.weight != 1:
                    raise ValueError(
                        "primary variables must precede auxiliaries and have unit weight"
                    )
                if not variable.job_id:
                    raise ValueError("primary variables require a job ID")
                if variable.kind == "assignment":
                    pair = (variable.job_id, variable.worker_id)
                    if not variable.worker_id or pair in seen_pairs:
                        raise ValueError("assignments need unique job/worker pairs")
                    seen_pairs.add(pair)
                elif variable.worker_id is not None or variable.job_id in seen_jobs:
                    raise ValueError("unassigned variables need unique jobs and no worker")
                else:
                    seen_jobs.add(variable.job_id)
            else:
                saw_auxiliary = True
                if variable.index not in auxiliary_indices:
                    raise ValueError(
                        "every auxiliary/slack variable must belong to an integer group"
                    )
        if len(seen_pairs) + len(self.removed_assignments) != self.original_possible_assignments:
            raise ValueError("preprocessing counts do not account for all original pairs")
        return self

    @property
    def primary_variable_count(self) -> int:
        return sum(v.kind in ("assignment", "unassigned") for v in self.variables)


class QUBOConfig(FrozenModel):
    objective: ObjectiveWeights = Field(default_factory=ObjectiveWeights)
    penalty_scale: str = "1"
    penalty_margin: int = Field(default=1, ge=1, strict=True)
    max_variables: int = Field(default=256, ge=0, strict=True)

    @field_validator("penalty_scale", mode="before")
    @classmethod
    def rational_scale(cls, value: object) -> str:
        if isinstance(value, bool):
            raise ValueError("penalty_scale must be a positive rational")
        try:
            scale = Fraction(str(value))
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError("penalty_scale must be a positive finite rational") from exc
        if scale <= 0:
            raise ValueError("penalty_scale must be positive")
        return str(scale)


class PenaltyMetadata(FrozenModel):
    business_upper_bound: int = Nonnegative
    safe_penalty: int = Field(ge=1, strict=True)
    applied_penalty: int = Field(ge=1, strict=True)
    safety_guaranteed: bool
    scale: str
    margin: int = Field(ge=1, strict=True)
    business_lower_bound: Literal[0] = 0

    @field_validator("scale", mode="before")
    @classmethod
    def rational_scale(cls, value: object) -> str:
        return QUBOConfig.rational_scale(value)

    @model_validator(mode="after")
    def derived_penalty(self) -> Self:
        scale = Fraction(self.scale)
        scaled = Fraction(self.safe_penalty) * scale
        ceiling = -(-scaled.numerator // scaled.denominator)
        if scale <= 0 or self.safe_penalty != self.business_upper_bound + self.margin:
            raise ValueError("penalty metadata must use a positive derived strategy")
        if self.applied_penalty != ceiling:
            raise ValueError("applied penalty must be the ceiling of safe penalty times scale")
        if self.safety_guaranteed != (self.applied_penalty > self.business_upper_bound):
            raise ValueError("penalty safety flag does not agree with the strict bound")
        return self


class QUBOMetrics(FrozenModel):
    original_worker_job_combinations: int
    number_possible_assignments_before: int
    number_variables_after_preprocessing: int
    assignment_variable_count: int
    unassigned_variable_count: int
    slack_variable_count: int
    auxiliary_variable_count: int
    qubo_variable_count: int
    linear_term_count: int
    quadratic_term_count: int
    qubo_density: float
    preprocessing_reduction: int
    reduction_ratio: float
    max_coefficient: int
    min_nonzero_coefficient: int | None
    coefficient_dynamic_range: str | None


class QUBOModel(Polynomial):
    schema_version: Literal[1] = 1
    mapping: VariableMapping
    config: QUBOConfig
    penalties: PenaltyMetadata
    components: dict[str, Polynomial]

    @model_validator(mode="after")
    def consistent_components(self) -> Self:
        if self.number_variables != len(self.mapping.variables):
            raise ValueError("model dimension and mapping disagree")
        if (self.config.penalty_scale, self.config.penalty_margin) != (
            self.penalties.scale,
            self.penalties.margin,
        ):
            raise ValueError("model config and penalty derivation disagree")
        names = {
            "business",
            "coverage",
            "mandatory",
            "overlap",
            "workload",
            "imbalance_constraints",
        }
        if set(self.components) != names:
            raise ValueError("all six named energy components are required")
        builder = PolynomialBuilder()
        for component in self.components.values():
            if component.number_variables != self.number_variables:
                raise ValueError("component dimension and model disagree")
            builder.add_polynomial(component)
        aggregate = builder.freeze(self.number_variables)
        if (self.linear, self.quadratic, self.constant) != (
            aggregate.linear,
            aggregate.quadratic,
            aggregate.constant,
        ):
            raise ValueError("aggregate polynomial differs from its energy components")
        return self

    def metrics(self) -> QUBOMetrics:
        kinds = [v.kind for v in self.mapping.variables]
        assignments = kinds.count("assignment")
        before = self.mapping.original_possible_assignments
        magnitudes = [abs(t.coefficient) for t in self.linear] + [
            abs(t.coefficient) for t in self.quadratic
        ]
        maximum = max(magnitudes, default=0)
        minimum = min(magnitudes, default=None)
        pairs = self.number_variables * (self.number_variables - 1) // 2
        return QUBOMetrics(
            original_worker_job_combinations=before,
            number_possible_assignments_before=before,
            number_variables_after_preprocessing=assignments,
            assignment_variable_count=assignments,
            unassigned_variable_count=kinds.count("unassigned"),
            slack_variable_count=kinds.count("slack"),
            auxiliary_variable_count=kinds.count("auxiliary"),
            qubo_variable_count=self.number_variables,
            linear_term_count=len(self.linear),
            quadratic_term_count=len(self.quadratic),
            qubo_density=len(self.quadratic) / pairs if pairs else 0.0,
            preprocessing_reduction=before - assignments,
            reduction_ratio=(before - assignments) / before if before else 0.0,
            max_coefficient=maximum,
            min_nonzero_coefficient=minimum,
            coefficient_dynamic_range=str(Fraction(maximum, minimum)) if minimum else None,
        )


class EnergyBreakdown(FrozenModel):
    total: int
    business: int
    coverage: int
    mandatory: int
    overlap: int
    workload: int
    imbalance_constraints: int

    @model_validator(mode="before")
    @classmethod
    def read_computed_total(cls, value: Any) -> Any:
        if isinstance(value, dict) and "constraint_total" in value:
            value = value.copy()
            declared = value.pop("constraint_total")
            if declared != value.get("total", 0) - value.get("business", 0):
                raise ValueError("constraint_total must agree with total minus business")
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def constraint_total(self) -> int:
        return self.total - self.business


def evaluate_qubo_energy(model: QUBOModel, bits: str | Sequence[int]) -> EnergyBreakdown:
    state = normalize_bits(bits, model.number_variables)
    values = {name: component.energy(state) for name, component in model.components.items()}
    return EnergyBreakdown(total=model.energy(state), **values)


class PolynomialBuilder:
    """Expand integer algebra with x_i^2=x_i, combining each unordered pair once."""

    def __init__(self) -> None:
        self.linear: dict[int, int] = {}
        self.quadratic: dict[tuple[int, int], int] = {}
        self.constant = 0

    def add_linear(self, index: int, coefficient: int) -> None:
        self.linear[index] = self.linear.get(index, 0) + coefficient

    def add_pair(self, left: int, right: int, coefficient: int) -> None:
        if left == right:
            self.add_linear(left, coefficient)
        else:
            pair = (min(left, right), max(left, right))
            self.quadratic[pair] = self.quadratic.get(pair, 0) + coefficient

    def add_square(self, coefficients: dict[int, int], constant: int, penalty: int) -> None:
        """Add penalty * (constant + sum_i coefficients[i]*x_i)^2 exactly."""
        self.constant += penalty * constant**2
        ordered = sorted(coefficients.items())
        for position, (index, coefficient) in enumerate(ordered):
            self.add_linear(index, penalty * (coefficient**2 + 2 * constant * coefficient))
            for other, value in ordered[position + 1 :]:
                self.add_pair(index, other, 2 * penalty * coefficient * value)

    def add_polynomial(self, model: Polynomial) -> None:
        self.constant += model.constant
        for term in model.linear:
            self.add_linear(term.index, term.coefficient)
        for quadratic_term in model.quadratic:
            self.add_pair(quadratic_term.left, quadratic_term.right, quadratic_term.coefficient)

    def freeze(self, size: int) -> Polynomial:
        return Polynomial(
            number_variables=size,
            constant=self.constant,
            linear=tuple(
                LinearTerm(index=i, coefficient=c) for i, c in sorted(self.linear.items()) if c
            ),
            quadratic=tuple(
                QuadraticTerm(left=i, right=j, coefficient=c)
                for (i, j), c in sorted(self.quadratic.items())
                if c
            ),
        )
