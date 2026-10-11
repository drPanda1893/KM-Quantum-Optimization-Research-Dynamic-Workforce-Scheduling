"""Independent amplitudes, estimator energies and visible optimizer accounting."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from qiskit.quantum_info import Statevector

from km_quantum.domain import SchedulingInstance
from km_quantum.experiments.io import canonical_hash
from km_quantum.qaoa import simulation
from km_quantum.qaoa.analysis import build_state_table
from km_quantum.qaoa.models import QAOAConfig
from km_quantum.qaoa.references import FrozenReference, load_references
from km_quantum.qaoa.simulation import (
    ExactEvaluator,
    bind_parameters,
    build_qaoa,
    circuit_metrics,
    run_qaoa,
    uniform_initial_state,
)
from km_quantum.qubo.encoder import encode_qubo
from km_quantum.qubo.ising import IsingModel, qubo_to_ising
from km_quantum.qubo.models import LinearTerm, QuadraticTerm

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def references() -> dict[str, FrozenReference]:
    return load_references(ROOT)


@pytest.mark.parametrize("number_qubits", [0, 1, 2, 4])
def test_uniform_initial_state_has_equal_real_amplitudes(number_qubits: int) -> None:
    circuit = uniform_initial_state(number_qubits)
    state = Statevector.from_instruction(circuit)
    np.testing.assert_allclose(state.data, np.full(2**number_qubits, 2 ** (-number_qubits / 2)))
    assert circuit.num_qubits == number_qubits


@pytest.mark.parametrize("p", [1, 2, 3])
@pytest.mark.parametrize("number_qubits", [1, 2])
def test_qaoa_matches_independent_cost_and_x_mixer_amplitudes(p: int, number_qubits: int) -> None:
    # Independently compute exp(-i beta X) exp(-i gamma H), without SDK gates.
    # The second field differs in sign and magnitude to expose reversed indices.
    h0, h1, coupling = 0.75, -1.25, 1.75
    ising = IsingModel(
        number_variables=number_qubits,
        constant4=19,
        fields=(LinearTerm(index=0, coefficient=3),)
        + ((LinearTerm(index=1, coefficient=-5),) if number_qubits == 2 else ()),
        couplings=(QuadraticTerm(left=0, right=1, coefficient=7),) if number_qubits == 2 else (),
    )
    prepared = build_qaoa(ising, p)
    expected_names = [f"β[{layer}]" for layer in range(p)] + [f"γ[{layer}]" for layer in range(p)]
    assert [parameter.name for parameter in prepared.circuit.parameters] == expected_names
    assert prepared.parameters == tuple(prepared.circuit.parameters)
    betas = (0.13, 0.31, 0.49)[:p]
    gammas = (0.27, 0.58, 0.83)[:p]
    binding = {
        **{f"β[{i}]": beta for i, beta in enumerate(betas)},
        **{f"γ[{i}]": gamma for i, gamma in enumerate(gammas)},
    }
    values = [binding[parameter.name] for parameter in prepared.parameters]
    bound = bind_parameters(prepared, values)
    assert not bound.parameters
    if number_qubits == 1:
        cost = np.array([h0, -h0])
    else:
        cost = np.array(
            [
                h0 + h1 + coupling,
                -h0 + h1 - coupling,
                h0 - h1 - coupling,
                -h0 - h1 + coupling,
            ]
        )
    expected = np.full(2**number_qubits, 2 ** (-number_qubits / 2), dtype=complex)
    for beta, gamma in zip(betas, gammas, strict=True):
        expected *= np.exp(-1j * gamma * cost)
        mixer = np.array(
            [
                [np.cos(beta), -1j * np.sin(beta)],
                [-1j * np.sin(beta), np.cos(beta)],
            ]
        )
        expected = (mixer if number_qubits == 1 else np.kron(mixer, mixer)) @ expected
    actual = Statevector.from_instruction(bound)
    np.testing.assert_allclose(actual.data, expected, rtol=1e-12, atol=1e-12)
    evaluator = ExactEvaluator(prepared)
    assert evaluator.evaluate(values) == pytest.approx(
        float(np.dot(np.abs(expected) ** 2, cost)) + 4.75, abs=1e-10
    )
    zero = Statevector.from_instruction(bind_parameters(prepared, [0.0] * (2 * p)))
    np.testing.assert_allclose(zero.probabilities(), np.full(2**number_qubits, 2**-number_qubits))


@pytest.mark.parametrize("p", [1, 2, 3])
def test_estimator_matches_complete_offset_inclusive_frozen_energy(
    references: dict[str, FrozenReference], p: int
) -> None:
    reference = references["A_single_job"]
    table = build_state_table(reference)
    prepared = build_qaoa(reference.ising, p)
    values = np.linspace(0.13, 0.91, len(prepared.parameters))
    evaluator = ExactEvaluator(prepared)
    observed = evaluator.evaluate(values)
    probabilities = Statevector.from_instruction(bind_parameters(prepared, values)).probabilities()
    assert observed == pytest.approx(float(np.dot(probabilities, table.energies)), abs=1e-10)
    trace = evaluator.trace[0]
    assert trace.expected_energy == trace.expectation + prepared.operator.energy_offset
    assert prepared.operator.energy_offset != 0


@pytest.mark.parametrize("p", [1, 2, 3])
def test_budget_trace_and_returned_final_distribution_agree(
    references: dict[str, FrozenReference], p: int, tmp_path: Path
) -> None:
    reference = references["A_single_job"]
    config = QAOAConfig(p=p, initialization_seed=37, maxiter=12)
    trace_path = tmp_path / "trace.jsonl"
    run, bound, probabilities = run_qaoa(reference, config, trace_path=trace_path)
    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert 1 <= run["nfev"] <= config.maxiter
    assert run["verification_evaluations"] == 1
    assert run["total_expectation_evaluations"] == run["nfev"] + 1 == len(records)
    assert [record["evaluation"] for record in records] == list(range(1, len(records) + 1))
    assert all(record["phase"] == "optimizer" for record in records[:-1])
    assert records[-1]["phase"] == "verification"
    assert records[0]["parameters"] == run["initial_parameters"]
    assert records[-1]["parameters"] == run["final_parameters"]
    assert records[-1]["expected_energy"] == run["final_expected_energy"]
    assert all(
        record["expected_energy"] == record["expectation"] + run["energy_offset"]
        for record in records
    )
    assert run["parameter_order"] == [
        parameter.name for parameter in build_qaoa(reference.ising, p).parameters
    ]
    assert all(0 <= value < np.pi for value in run["initial_parameters"])
    np.testing.assert_allclose(probabilities, Statevector.from_instruction(bound).probabilities())
    assert run["expectation_verified"] and run["operator_equivalence"]
    assert isinstance(run["success"], bool)
    assert run["termination_message"]
    json.dumps(run, allow_nan=False)
    with pytest.raises(ValueError, match="Trace already exists"):
        run_qaoa(reference, config, trace_path=trace_path)


def test_seed_repeats_initialization_optimization_and_final_probabilities(
    references: dict[str, FrozenReference], tmp_path: Path
) -> None:
    reference = references["A_single_job"]
    config = QAOAConfig(p=2, initialization_seed=51, maxiter=12)
    first, _, probabilities_a = run_qaoa(reference, config, trace_path=tmp_path / "a.jsonl")
    second, _, probabilities_b = run_qaoa(reference, config, trace_path=tmp_path / "b.jsonl")
    for field in (
        "initial_parameters",
        "final_parameters",
        "final_expected_energy",
        "nfev",
        "success",
        "termination_message",
        "ground_state_probability",
    ):
        assert first[field] == second[field]
    np.testing.assert_array_equal(probabilities_a, probabilities_b)
    third, _, _ = run_qaoa(reference, config.model_copy(update={"initialization_seed": 52}))
    assert third["initial_parameters"] != first["initial_parameters"]


def test_too_small_optimizer_budget_is_rejected_without_starting_trace(
    references: dict[str, FrozenReference], tmp_path: Path
) -> None:
    trace = tmp_path / "must-not-exist.jsonl"
    with pytest.raises(ValueError, match="parameter_count"):
        run_qaoa(references["A_single_job"], QAOAConfig(p=3, maxiter=7), trace_path=trace)
    assert not trace.exists()


def test_failed_operator_gate_prevents_ansatz_construction(
    references: dict[str, FrozenReference], monkeypatch: pytest.MonkeyPatch
) -> None:
    simulation._verified_operator.cache_clear()

    def failed_gate(*args: object, **kwargs: object) -> dict[str, Any]:
        return {"passed": False}

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("An ansatz must not be constructed after a failed gate")

    monkeypatch.setattr(simulation, "verify_operator", failed_gate)
    monkeypatch.setattr(simulation, "build_qaoa", forbidden)
    try:
        with pytest.raises(ValueError, match="operator gate"):
            run_qaoa(references["A_single_job"], QAOAConfig(maxiter=8))
    finally:
        simulation._verified_operator.cache_clear()


def test_run_rejects_state_table_from_other_reference(
    references: dict[str, FrozenReference],
) -> None:
    reference = references["A_single_job"]
    table = replace(build_state_table(reference), reference_hash="wrong")
    with pytest.raises(ValueError, match="different frozen reference"):
        run_qaoa(reference, QAOAConfig(maxiter=8), table=table)


def test_zero_qubit_run_has_only_one_verification_evaluation(tmp_path: Path) -> None:
    instance = SchedulingInstance(instance_id="empty-simulation", seed=0, workers=(), jobs=())
    model = encode_qubo(instance)
    ising = qubo_to_ising(model)
    reference = FrozenReference(
        name="empty-simulation",
        instance=instance,
        qubo=model,
        ising=ising,
        ground_bits=("",),
        ground_energy=0,
        constraint_energy_margin=None,
        reference_hash="empty-simulation",
        qubo_hash=canonical_hash(model),
        ising_hash=canonical_hash(ising),
    )
    path = tmp_path / "empty.jsonl"
    run, bound, probabilities = run_qaoa(reference, QAOAConfig(maxiter=1), trace_path=path)
    assert bound.num_qubits == run["qubit_count"] == 0
    assert run["parameter_count"] == run["nfev"] == 0
    assert run["total_expectation_evaluations"] == run["verification_evaluations"] == 1
    assert json.loads(path.read_text(encoding="utf-8"))["phase"] == "verification"
    assert run["final_expected_energy"] == 0
    assert run["ground_state_probability"] == run["normalized_quality"] == 1
    np.testing.assert_array_equal(probabilities, [1.0])


@pytest.mark.parametrize("p", [1, 2, 3])
def test_constant_hamiltonian_has_uniform_probabilities_and_exact_energy(p: int) -> None:
    prepared = build_qaoa(IsingModel(number_variables=2, constant4=17), p)
    assert prepared.parameters == ()
    values: list[float] = []
    bound = bind_parameters(prepared, values)
    np.testing.assert_allclose(Statevector.from_instruction(bound).probabilities(), [0.25] * 4)
    assert ExactEvaluator(prepared).evaluate(values) == 4.25


def test_build_binding_and_logical_metrics_validate_inputs() -> None:
    ising = IsingModel(
        number_variables=2, constant4=0, fields=(LinearTerm(index=0, coefficient=4),)
    )
    prepared = build_qaoa(ising, 2)
    for values in ([0.0], [0.0, 0.0, 0.0, float("nan")], [0.0, 0.0, 0.0, float("inf")]):
        with pytest.raises(ValueError, match="finite vector"):
            bind_parameters(prepared, values)
    for p in (0, 4, True):
        with pytest.raises(ValueError, match="p=1,2,3"):
            build_qaoa(ising, p)
    with pytest.raises(ValueError, match="max_qubits"):
        build_qaoa(ising, 1, max_qubits=1)
    with pytest.raises(ValueError, match="nonnegative"):
        uniform_initial_state(True)
    metrics = circuit_metrics(prepared)
    assert metrics["parameter_order"] == [
        parameter.name for parameter in prepared.circuit.parameters
    ]
    assert metrics["gate_count"] == sum(metrics["gate_histogram"].values())
    assert metrics["gate_count"] == sum(
        metrics[key]
        for key in (
            "one_qubit_gate_count",
            "two_qubit_gate_count",
            "other_gate_count",
        )
    )
    assert metrics["logical_depth"] > 0
    assert not metrics["hardware_transpilation"]
