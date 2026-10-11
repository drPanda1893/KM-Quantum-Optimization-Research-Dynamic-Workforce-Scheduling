"""Independent convexity, binary continuation, gradient and strict-budget checks."""

import itertools
import json
from fractions import Fraction

import numpy as np
import pytest

from km_quantum.m4.relaxation import (
    RelaxationConfig,
    build_box_relaxation,
    solve_box_relaxation,
)
from km_quantum.qubo.models import LinearTerm, Polynomial, QuadraticTerm


@pytest.fixture
def polynomial() -> Polynomial:
    return Polynomial(
        number_variables=3,
        constant=7,
        linear=(
            LinearTerm(index=0, coefficient=-4),
            LinearTerm(index=1, coefficient=3),
            LinearTerm(index=2, coefficient=-2),
        ),
        quadratic=(
            QuadraticTerm(left=0, right=1, coefficient=6),
            QuadraticTerm(left=0, right=2, coefficient=-5),
            QuadraticTerm(left=1, right=2, coefficient=3),
        ),
    )


@pytest.mark.parametrize("size", range(6))
def test_convex_continuation_preserves_every_binary_energy(size: int) -> None:
    rng = np.random.default_rng(330 + size)
    linear = tuple(
        LinearTerm(index=i, coefficient=value)
        for i in range(size)
        if (value := int(rng.integers(-10, 11)))
    )
    pairs = tuple(
        QuadraticTerm(left=i, right=j, coefficient=value)
        for i in range(size)
        for j in range(i + 1, size)
        if (value := int(rng.integers(-10, 11)))
    )
    polynomial = Polynomial(number_variables=size, constant=-7, linear=linear, quadratic=pairs)
    relaxation = build_box_relaxation(polynomial)
    for bits in itertools.product((0, 1), repeat=size):
        value, _ = relaxation.value_and_gradient(bits)
        scaled, _ = relaxation.value_and_gradient(bits, scaled=True)
        assert value == pytest.approx(polynomial.energy(bits), abs=1e-12)
        assert scaled * relaxation.numerical_scale == pytest.approx(value, abs=1e-12)


def test_psd_bound_uses_exact_half_pair_rows_and_a_documented_safe_ulp(
    polynomial: Polynomial,
) -> None:
    relaxation = build_box_relaxation(polynomial)
    # Independent row sums: (6+5)/2, (6+3)/2, (5+3)/2.
    assert relaxation.exact_row_bound == Fraction(11, 2)
    assert Fraction.from_float(relaxation.lambda_value) >= Fraction(11, 2)
    assert relaxation.lambda_value == np.nextafter(5.5, np.inf)
    np.testing.assert_array_equal(
        relaxation.quadratic - np.eye(3) * relaxation.lambda_value,
        [[0, 3, -2.5], [3, 0, 1.5], [-2.5, 1.5, 0]],
    )
    assert np.min(np.linalg.eigvalsh(relaxation.quadratic)) >= -1e-12
    assert relaxation.minimum_eigenvalue == pytest.approx(
        np.min(np.linalg.eigvalsh(relaxation.quadratic))
    )
    with pytest.raises(ValueError, match="read-only"):
        relaxation.linear[0] = 99


@pytest.mark.parametrize("scaled", [False, True])
def test_analytic_gradient_matches_independent_central_differences(
    polynomial: Polynomial, scaled: bool
) -> None:
    relaxation = build_box_relaxation(polynomial)
    point = np.array([0.21, 0.57, 0.84])
    _, gradient = relaxation.value_and_gradient(point, scaled=scaled)
    finite = np.empty(3)
    for index in range(3):
        step = np.zeros(3)
        step[index] = 1e-6
        right, _ = relaxation.value_and_gradient(point + step, scaled=scaled)
        left, _ = relaxation.value_and_gradient(point - step, scaled=scaled)
        finite[index] = (right - left) / 2e-6
    np.testing.assert_allclose(gradient, finite, atol=1e-8, rtol=1e-8)


def test_linear_box_solution_has_correct_kkt_and_tangent_bound() -> None:
    polynomial = Polynomial(
        number_variables=2,
        constant=9,
        linear=(LinearTerm(index=0, coefficient=-4), LinearTerm(index=1, coefficient=3)),
    )
    relaxation = build_box_relaxation(polynomial)
    assert relaxation.lambda_value == 0
    # From any point, the affine supporting function is the exact linear objective.
    diagnostics = relaxation.diagnostics([0.2, 0.6])
    assert diagnostics["convex_tangent_lower_bound"] == pytest.approx(5)
    assert diagnostics["projected_gradient_inf"] > 0
    result = solve_box_relaxation(polynomial)
    assert result["status"] == "CONVERGED" and result["library_success"]
    assert result["numerically_stationary"]
    assert result["y"] == [1, 0]
    assert result["objective_value"] == 5
    assert result["convex_tangent_gap"] == 0
    assert result["scaled_projected_gradient_inf"] == 0
    assert 1 <= result["function_evaluations"] <= 300
    assert result["gradient_evaluations"] == result["function_evaluations"]


def test_convex_tangent_bound_is_below_all_binary_values(polynomial: Polynomial) -> None:
    relaxation = build_box_relaxation(polynomial)
    for point in ([0.2, 0.6, 0.9], [0, 1, 0], [0.5, 0.5, 0.5]):
        diagnostics = relaxation.diagnostics(point)
        lower = diagnostics["convex_tangent_lower_bound"]
        value, _ = relaxation.value_and_gradient(point)
        assert lower <= value + 1e-12
        for bits in itertools.product((0, 1), repeat=3):
            assert lower <= polynomial.energy(bits) + 1e-12


def test_hard_budget_cannot_be_exceeded_and_has_no_hidden_oracle_fallback(
    polynomial: Polynomial,
) -> None:
    result = solve_box_relaxation(polynomial, RelaxationConfig(max_function_evaluations=1))
    assert result["status"] == "BUDGET_EXHAUSTED"
    assert not result["library_success"]
    assert result["function_evaluations"] == result["gradient_evaluations"] == 1
    assert result["y"] == [0.5, 0.5, 0.5]
    assert len(result["trace"]) == 1
    assert result["termination_message"]
    json.dumps(result, allow_nan=False)


def test_iteration_limit_is_persisted_as_unsuccessful(polynomial: Polynomial) -> None:
    result = solve_box_relaxation(polynomial, RelaxationConfig(max_iterations=1))
    assert result["status"] == "ITERATION_LIMIT"
    assert not result["library_success"]
    assert result["iterations"] == 1
    assert result["function_evaluations"] <= 300


def test_repeated_relaxations_have_identical_nontiming_results(polynomial: Polynomial) -> None:
    first, second = solve_box_relaxation(polynomial), solve_box_relaxation(polynomial)
    first.pop("runtime_ms")
    second.pop("runtime_ms")
    assert first == second


def test_empty_and_constant_boxes_are_honest_without_invented_information() -> None:
    empty = solve_box_relaxation(Polynomial(number_variables=0, constant=7))
    assert empty["y"] == [] and empty["function_evaluations"] == 0
    assert empty["objective_value"] == 7 and empty["convex_tangent_gap"] == 0
    constant = solve_box_relaxation(Polynomial(number_variables=3, constant=7))
    assert constant["y"] == [0.5, 0.5, 0.5]
    assert constant["lambda"] == 0 and constant["numerically_stationary"]


@pytest.mark.parametrize("point", [[0.2], [0.2, 0.3, float("nan")], [-0.1, 0.2, 0.3]])
def test_invalid_relaxation_points_are_rejected(polynomial: Polynomial, point: list) -> None:
    with pytest.raises(ValueError):
        build_box_relaxation(polynomial).value_and_gradient(point)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_function_evaluations": 0},
        {"max_iterations": True},
        {"gtol": float("nan")},
        {"ftol": 0},
        {"max_variables": 11},
    ],
)
def test_invalid_configs_are_rejected(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        RelaxationConfig(**kwargs)


def test_variable_limit_and_unrepresentable_polynomials_are_rejected() -> None:
    with pytest.raises(ValueError, match="limit"):
        build_box_relaxation(Polynomial(number_variables=11))
    with pytest.raises(ValueError, match="exact float"):
        build_box_relaxation(Polynomial(number_variables=1, constant=2**54 + 1))
