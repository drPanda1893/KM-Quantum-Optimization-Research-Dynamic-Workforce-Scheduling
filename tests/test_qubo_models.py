"""Independent QUBO algebra, schema and numerical representation checks."""

from itertools import product

import pytest
from pydantic import ValidationError

from km_quantum.qubo.encoder import bounded_weights
from km_quantum.qubo.models import (
    EnergyBreakdown,
    LinearTerm,
    PenaltyMetadata,
    Polynomial,
    PolynomialBuilder,
    QuadraticTerm,
    QUBOConfig,
    normalize_bits,
)


@pytest.mark.parametrize("constant", [-3, 0, 11, 10**400])
def test_square_expansion_all_states(constant):
    coefficients = {0: 3, 1: -2, 2: 5}
    builder = PolynomialBuilder()
    builder.add_square(coefficients, constant, 7)
    model = builder.freeze(3)
    for bits in product((0, 1), repeat=3):
        expected = 7 * (constant + sum(coefficients[i] * bits[i] for i in range(3))) ** 2
        assert model.energy(bits) == expected
    assert Polynomial.model_validate_json(model.model_dump_json()) == model


def test_collect_terms_and_diagonal_identity():
    builder = PolynomialBuilder()
    builder.add_linear(0, 5)
    builder.add_pair(0, 0, -2)
    builder.add_pair(0, 1, 7)
    builder.add_pair(1, 0, -3)
    builder.add_linear(1, 4)
    builder.add_linear(1, -4)
    model = builder.freeze(2)
    assert model.linear == (LinearTerm(index=0, coefficient=3),)
    assert model.quadratic == (QuadraticTerm(left=0, right=1, coefficient=4),)
    assert model.to_matrix() == [[3, 4], [0, 0]]
    for x in product((0, 1), repeat=2):
        assert model.energy(x) == sum(
            x[i] * model.to_matrix()[i][j] * x[j] for i in range(2) for j in range(2)
        )


@pytest.mark.parametrize("bound", range(32))
def test_bounded_integer_full_range(bound):
    weights = bounded_weights(bound)
    values = {
        sum(w * b for w, b in zip(weights, bits, strict=True))
        for bits in product((0, 1), repeat=len(weights))
    }
    assert values == set(range(bound + 1))
    assert len(weights) == bound.bit_length()


@pytest.mark.parametrize("bits,size", [("0x", 2), ("10", 3), ([True], 1), ([2], 1), ([0.0], 1)])
def test_reject_malformed_bits(bits, size):
    with pytest.raises(ValueError):
        normalize_bits(bits, size)


@pytest.mark.parametrize(
    "fields",
    [
        {"number_variables": 1, "linear": [{"index": 1, "coefficient": 1}]},
        {"number_variables": 1, "linear": [{"index": 0, "coefficient": 0}]},
        {"number_variables": 1, "linear": [{"index": 0, "coefficient": 1}] * 2},
        {"number_variables": 2, "quadratic": [{"left": 1, "right": 0, "coefficient": 1}]},
        {"number_variables": 2, "quadratic": [{"left": 0, "right": 0, "coefficient": 1}]},
        {"number_variables": 2, "quadratic": [{"left": 0, "right": 2, "coefficient": 1}]},
        {"number_variables": True},
        {"number_variables": 0, "constant": 1.1},
    ],
)
def test_polynomial_schema_rejects_noncanonical_or_float_terms(fields):
    with pytest.raises(ValidationError):
        Polynomial.model_validate(fields)


@pytest.mark.parametrize("scale", ["0", "-1", "nan", "inf", True, "1/0"])
def test_invalid_scales(scale):
    with pytest.raises(ValidationError):
        QUBOConfig(penalty_scale=scale)
    with pytest.raises(ValidationError):
        PenaltyMetadata(
            business_upper_bound=4,
            safe_penalty=5,
            applied_penalty=5,
            safety_guaranteed=True,
            scale=scale,
            margin=1,
        )


def test_rational_scale_canonicalization_and_penalty_derivation():
    assert QUBOConfig(penalty_scale="0.25").penalty_scale == "1/4"
    assert QUBOConfig(penalty_scale="2/8").penalty_scale == "1/4"
    safe = dict(
        business_upper_bound=4,
        safe_penalty=5,
        applied_penalty=5,
        safety_guaranteed=True,
        scale="1",
        margin=1,
    )
    assert PenaltyMetadata(**safe).business_lower_bound == 0
    for field, value in [("safe_penalty", 4), ("applied_penalty", 4), ("safety_guaranteed", False)]:
        with pytest.raises(ValidationError):
            PenaltyMetadata.model_validate({**safe, field: value})


def test_energy_breakdown_roundtrip_checks_computed_field():
    energy = EnergyBreakdown(
        total=9, business=4, coverage=5, mandatory=0, overlap=0, workload=0, imbalance_constraints=0
    )
    assert EnergyBreakdown.model_validate_json(energy.model_dump_json()) == energy
    with pytest.raises(ValidationError):
        EnergyBreakdown.model_validate({**energy.model_dump(), "constraint_total": 0})


@pytest.mark.parametrize("field", ["total", "business", "constraint_total"])
def test_malformed_serialized_energy_totals_report_schema_error(field):
    energy = EnergyBreakdown(
        total=9, business=4, coverage=5, mandatory=0, overlap=0, workload=0, imbalance_constraints=0
    )
    with pytest.raises(ValidationError):
        EnergyBreakdown.model_validate({**energy.model_dump(), field: "bad"})
