"""One convex, binary-equivalent box relaxation, without reference-oracle access."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from fractions import Fraction
from math import isfinite
from time import perf_counter
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import minimize

from km_quantum.qubo.models import Polynomial


@dataclass(frozen=True)
class RelaxationConfig:
    max_function_evaluations: int = 300
    max_iterations: int = 300
    ftol: float = 1e-12
    gtol: float = 1e-8
    max_variables: int = 10

    def __post_init__(self) -> None:
        for name in ("max_function_evaluations", "max_iterations"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if type(self.max_variables) is not int or not 0 <= self.max_variables <= 10:
            raise ValueError("M4 relaxation supports at most ten variables")
        for name in ("ftol", "gtol"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")


def _exact_float(value: Fraction, name: str) -> float:
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} must fit a finite exact float") from error
    if not isfinite(result) or Fraction.from_float(result) != value:
        raise ValueError(f"{name} must fit a finite exact float")
    return result


def _box_vector(values: Sequence[float] | NDArray[np.float64], size: int) -> NDArray[np.float64]:
    vector = np.asarray(values, dtype=np.float64)
    if vector.shape != (size,) or not np.all(np.isfinite(vector)):
        raise ValueError("The relaxation point must be a finite vector of the correct dimension")
    if np.any(vector < 0) or np.any(vector > 1):
        raise ValueError("The relaxation point must lie in [0,1]")
    return vector


@dataclass(frozen=True)
class BoxRelaxation:
    constant: float
    linear: NDArray[np.float64]
    quadratic: NDArray[np.float64]
    lambda_value: float
    exact_row_bound: Fraction
    numerical_scale: float
    minimum_eigenvalue: float

    @property
    def number_variables(self) -> int:
        return len(self.linear)

    def value_and_gradient(
        self, values: Sequence[float] | NDArray[np.float64], *, scaled: bool = False
    ) -> tuple[float, NDArray[np.float64]]:
        vector = _box_vector(values, self.number_variables)
        shifted = self.linear - self.lambda_value
        value = self.constant + float(shifted @ vector + vector @ self.quadratic @ vector)
        gradient = shifted + 2 * self.quadratic @ vector
        if scaled:
            value /= self.numerical_scale
            gradient = gradient / self.numerical_scale
        if not isfinite(value) or not np.all(np.isfinite(gradient)):
            raise ValueError("The relaxation energy or gradient is nonfinite")
        return value, gradient

    def diagnostics(self, values: Sequence[float] | NDArray[np.float64]) -> dict[str, float]:
        vector = _box_vector(values, self.number_variables)
        value, gradient = self.value_and_gradient(vector)
        projected = vector - np.clip(vector - gradient, 0, 1)
        scaled_projected = vector - np.clip(vector - gradient / self.numerical_scale, 0, 1)
        # Minimize the supporting affine function over the box, independently of the solver.
        gap = float(np.sum(np.maximum(gradient, 0) * vector)) + float(
            np.sum(np.maximum(-gradient, 0) * (1 - vector))
        )
        return {
            "projected_gradient_inf": float(np.max(np.abs(projected), initial=0)),
            "scaled_projected_gradient_inf": float(np.max(np.abs(scaled_projected), initial=0)),
            "convex_tangent_lower_bound": value - gap,
            "convex_tangent_gap": gap,
            "scaled_convex_tangent_gap": gap / self.numerical_scale,
        }


def build_box_relaxation(polynomial: Polynomial, *, max_variables: int = 10) -> BoxRelaxation:
    """Add lambda*(y_i^2-y_i); only the classical continuous extension changes."""
    if type(max_variables) is not int or not 0 <= max_variables <= 10:
        raise ValueError("M4 relaxation supports at most ten variables")
    size = polynomial.number_variables
    if size > max_variables:
        raise ValueError("The relaxation exceeds its variable limit")
    linear = np.zeros(size, dtype=np.float64)
    quadratic = np.zeros((size, size), dtype=np.float64)
    exact_rows = [Fraction(0) for _ in range(size)]
    for term in polynomial.linear:
        linear[term.index] = _exact_float(Fraction(term.coefficient), "Linear coefficient")
    for pair in polynomial.quadratic:
        exact = Fraction(pair.coefficient, 2)
        value = _exact_float(exact, "Half-pair coefficient")
        quadratic[pair.left, pair.right] = value
        quadratic[pair.right, pair.left] = value
        exact_rows[pair.left] += abs(exact)
        exact_rows[pair.right] += abs(exact)
    exact_row_bound = max(exact_rows, default=Fraction(0))
    bound = _exact_float(exact_row_bound, "Exact Gershgorin row bound")
    # One ULP above the exact bound for nonzero interactions; avoid invented curvature at zero.
    lambda_value = float(np.nextafter(bound, np.inf)) if bound else 0.0
    if not isfinite(lambda_value) or Fraction.from_float(lambda_value) < exact_row_bound:
        raise ValueError("The safe lambda does not fit a finite float")
    if size:
        quadratic[np.diag_indices(size)] = lambda_value
    constant = _exact_float(Fraction(polynomial.constant), "Constant")
    shifted = linear - lambda_value
    # Positive scaling applies only to the classical optimizer, never to the QUBO/Ising cost.
    numerical_scale = max(
        1.0,
        abs(constant),
        float(np.max(np.abs(shifted), initial=0)),
        2 * float(np.max(np.sum(np.abs(quadratic), axis=1), initial=0)),
    )
    if not isfinite(numerical_scale):
        raise ValueError("The relaxation numerical scale is nonfinite")
    minimum_eigenvalue = float(np.min(np.linalg.eigvalsh(quadratic))) if size else 0.0
    linear.setflags(write=False)
    quadratic.setflags(write=False)
    return BoxRelaxation(
        constant,
        linear,
        quadratic,
        lambda_value,
        exact_row_bound,
        numerical_scale,
        minimum_eigenvalue,
    )


class _BudgetExhausted(Exception):
    pass


def solve_box_relaxation(
    polynomial: Polynomial, config: RelaxationConfig | None = None
) -> dict[str, Any]:
    """Deterministic L-BFGS-B, analytic gradient, and a hard combined f/g call budget.

    A budget failure is persisted with its best evaluated point and failure status.
    No exact scheduling solution or binary ground state enters this data path.
    """
    started = perf_counter()
    config = config or RelaxationConfig()
    relaxation = build_box_relaxation(polynomial, max_variables=config.max_variables)
    initial = np.full(polynomial.number_variables, 0.5, dtype=np.float64)
    best = initial.copy()
    best_value = float("inf")
    evaluations = 0
    trace: list[dict[str, Any]] = []

    def evaluate(values: NDArray[np.float64]) -> tuple[float, NDArray[np.float64]]:
        nonlocal evaluations, best, best_value
        if evaluations >= config.max_function_evaluations:
            raise _BudgetExhausted
        value, gradient = relaxation.value_and_gradient(values, scaled=True)
        evaluations += 1
        if value < best_value:
            best, best_value = values.copy(), value
        trace.append({"evaluation": evaluations, "y": values.tolist(), "scaled_value": value})
        return value, gradient

    if polynomial.number_variables == 0:
        success, status, message, iterations = True, "CONVERGED", "Empty box", 0
    else:
        try:
            optimized = minimize(
                evaluate,
                initial,
                jac=True,
                method="L-BFGS-B",
                bounds=[(0.0, 1.0)] * polynomial.number_variables,
                options={
                    "maxfun": config.max_function_evaluations,
                    "maxiter": config.max_iterations,
                    "ftol": config.ftol,
                    "gtol": config.gtol,
                    "maxls": 20,
                },
            )
            success = bool(optimized.success)
            message = str(optimized.message)
            iterations = int(optimized.nit)
            if success:
                status = "CONVERGED"
            elif evaluations >= config.max_function_evaluations:
                status = "BUDGET_EXHAUSTED"
            elif iterations >= config.max_iterations:
                status = "ITERATION_LIMIT"
            else:
                status = "FAILED"
        except _BudgetExhausted:
            success, status, message, iterations = (
                False,
                "BUDGET_EXHAUSTED",
                "Hard combined objective/gradient evaluation budget reached",
                None,
            )
    value, _ = relaxation.value_and_gradient(best)
    diagnostics = relaxation.diagnostics(best)
    result: dict[str, Any] = {
        "schema_version": 1,
        "method": "convex_diagonal_box_L-BFGS-B",
        "status": status,
        "library_success": success,
        "termination_message": message,
        "y": best.tolist(),
        "initial_y": initial.tolist(),
        "lambda": relaxation.lambda_value,
        "exact_row_bound": str(relaxation.exact_row_bound),
        "lambda_safety": "nextafter(exact_row_bound,+inf), or zero for zero row bound",
        "numerical_scale": relaxation.numerical_scale,
        "minimum_eigenvalue": relaxation.minimum_eigenvalue,
        "objective_value": value,
        "scaled_objective_value": value / relaxation.numerical_scale,
        "function_evaluations": evaluations,
        "gradient_evaluations": evaluations,
        "iterations": iterations,
        "config": asdict(config),
        "numerically_stationary": diagnostics["scaled_projected_gradient_inf"] <= config.gtol,
        "diagnostic_certificate": "floating-point convex tangent bound; not a symbolic certificate",
        "trace": trace,
        **diagnostics,
        "runtime_ms": (perf_counter() - started) * 1000,
    }
    json.dumps(result, allow_nan=False)
    return result
