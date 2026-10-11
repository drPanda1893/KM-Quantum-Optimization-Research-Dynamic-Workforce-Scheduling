"""Same-budget ideal training, complete persisted traces, and preparation failure gates."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from qiskit.quantum_info import Statevector

from km_quantum.domain import SchedulingInstance
from km_quantum.experiments.io import canonical_hash
from km_quantum.m4 import training
from km_quantum.m4.models import M4Config
from km_quantum.m4.relaxation import solve_box_relaxation
from km_quantum.m4.training import train_variant
from km_quantum.qaoa.analysis import StateTable, build_state_table
from km_quantum.qaoa.models import QAOAConfig
from km_quantum.qaoa.references import FrozenReference, load_references
from km_quantum.qaoa.simulation import run_qaoa
from km_quantum.qubo.encoder import encode_qubo
from km_quantum.qubo.ising import qubo_to_ising

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def reference() -> FrozenReference:
    return load_references(ROOT)["A_single_job"]


@pytest.fixture(scope="module")
def table(reference: FrozenReference) -> StateTable:
    return build_state_table(reference)


@pytest.fixture(scope="module")
def relaxation(reference: FrozenReference) -> dict[str, Any]:
    return solve_box_relaxation(reference.qubo)


@pytest.mark.parametrize("algorithm", ["standard", "warm_start"])
@pytest.mark.parametrize("p", [1, 2])
def test_shared_initialization_budget_and_final_trace_distribution_agree(
    reference: FrozenReference,
    table: StateTable,
    relaxation: dict[str, Any],
    tmp_path: Path,
    algorithm: str,
    p: int,
) -> None:
    config = M4Config(optimizer_maxiter=12)
    path = tmp_path / "trace.jsonl"
    run, bound, probabilities = train_variant(
        reference,
        algorithm=algorithm,  # type: ignore[arg-type]
        p=p,
        seed=2,
        config=config,
        trace_path=path,
        relaxation=relaxation if algorithm == "warm_start" else None,
        table=table,
    )
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    expected = np.random.default_rng(2).uniform(0.0, np.pi, run["parameter_count"])
    np.testing.assert_array_equal(run["initial_parameters"], expected)
    assert run["status"] == "COMPLETE" and run["algorithm"] == algorithm
    assert run["optimizer_config"] == {"maxiter": 12, "tolerance": 1e-6, "rhobeg": 0.5}
    assert 1 <= run["nfev"] <= 12
    assert run["verification_evaluations"] == 1
    assert len(records) == run["total_expectation_evaluations"] == run["nfev"] + 1
    assert [record["evaluation"] for record in records] == list(range(1, len(records) + 1))
    assert all(record["phase"] == "optimizer" for record in records[:-1])
    assert records[-1]["phase"] == "verification"
    assert records[0]["parameters"] == run["initial_parameters"]
    assert records[-1]["parameters"] == run["final_parameters"]
    assert run["parameter_order"] == [f"β[{i}]" for i in range(p)] + [f"γ[{i}]" for i in range(p)]
    assert run["final_expected_energy"] == records[-1]["expected_energy"]
    assert run["final_expected_energy"] == pytest.approx(
        float(probabilities @ table.energies), abs=1e-10
    )
    np.testing.assert_allclose(probabilities, Statevector.from_instruction(bound).probabilities())
    assert run["expectation_verified"] and run["operator_equivalence"]
    assert run["mixer_sign"] == 1 and "unchanged" in run["hamiltonian_scale"]
    assert "ideal exact" in run["training_cost"]
    assert run["runtime_ms"] >= 0
    for key, value in run["timings"].items():
        if key != "scope":
            assert value >= 0
    json.dumps(run, allow_nan=False)


def test_standard_path_is_numerically_identical_to_untouched_m3_engine(
    reference: FrozenReference, table: StateTable, tmp_path: Path
) -> None:
    config = M4Config(optimizer_maxiter=12)
    actual, _, probabilities = train_variant(
        reference,
        algorithm="standard",
        p=2,
        seed=3,
        config=config,
        trace_path=tmp_path / "m4.jsonl",
        table=table,
    )
    original, _, original_probabilities = run_qaoa(
        reference,
        QAOAConfig(
            p=2,
            initialization_seed=3,
            maxiter=12,
            max_qubits=10,
            sampler_seed=config.sample_seed_base + 203,
        ),
        trace_path=tmp_path / "m3.jsonl",
        table=table,
    )
    for key in (
        "initial_parameters",
        "final_parameters",
        "final_expected_energy",
        "nfev",
        "success",
        "termination_message",
        "parameter_order",
        "energy_offset",
    ):
        assert actual[key] == original[key]
    np.testing.assert_array_equal(probabilities, original_probabilities)


def test_warm_seed_repeats_angles_optimization_and_probabilities(
    reference: FrozenReference, table: StateTable, relaxation: dict[str, Any], tmp_path: Path
) -> None:
    common = dict(
        reference=reference,
        algorithm="warm_start",
        p=2,
        seed=1,
        config=M4Config(optimizer_maxiter=12),
        table=table,
        relaxation=relaxation,
    )
    first, _, p_first = train_variant(**common, trace_path=tmp_path / "one.jsonl")
    second, _, p_second = train_variant(**common, trace_path=tmp_path / "two.jsonl")
    for key in (
        "initial_parameters",
        "final_parameters",
        "final_expected_energy",
        "nfev",
        "success",
        "termination_message",
    ):
        assert first[key] == second[key]
    np.testing.assert_array_equal(p_first, p_second)


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "BUDGET_EXHAUSTED"},
        {"status": "FAILED"},
        {"library_success": False},
    ],
)
def test_failed_preprocessing_blocks_all_warm_circuit_work_and_creates_no_trace(
    reference: FrozenReference,
    relaxation: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, Any],
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("No circuit should be built after failed preparation")

    monkeypatch.setattr(training, "build_warm_qaoa", forbidden)
    path = tmp_path / "not-created.jsonl"
    with pytest.raises(ValueError, match="preparation did not converge"):
        train_variant(
            reference,
            algorithm="warm_start",
            p=1,
            seed=0,
            config=M4Config(),
            trace_path=path,
            relaxation={**relaxation, **changes},
        )
    assert not path.exists()


def test_ftol_convergence_can_pass_prespecified_accuracy_gate_with_strict_flag_false(
    reference: FrozenReference, table: StateTable, relaxation: dict[str, Any], tmp_path: Path
) -> None:
    approximate = {
        **relaxation,
        "numerically_stationary": False,
        "scaled_projected_gradient_inf": 1.6e-7,
        "scaled_convex_tangent_gap": 4.25e-7,
    }
    run, _, _ = train_variant(
        reference,
        algorithm="warm_start",
        p=1,
        seed=0,
        config=M4Config(optimizer_maxiter=12),
        trace_path=tmp_path / "approximate.jsonl",
        table=table,
        relaxation=approximate,
    )
    assert run["preparation_quality_gate"]["passed"]
    assert run["preparation_quality_gate"]["threshold"] == 1e-6
    assert not run["preparation_quality_gate"]["numerically_stationary_at_solver_gtol"]
    assert run["relaxation"]["numerically_stationary"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"scaled_projected_gradient_inf": 1.1e-6},
        {"scaled_convex_tangent_gap": 1.1e-6},
        {"scaled_projected_gradient_inf": float("nan")},
        {"scaled_convex_tangent_gap": None},
        {"y": [float("nan")]},
    ],
)
def test_failed_approximate_preparation_gate_rejects_before_any_trace(
    reference: FrozenReference,
    relaxation: dict[str, Any],
    tmp_path: Path,
    changes: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="preparation"):
        train_variant(
            reference,
            algorithm="warm_start",
            p=1,
            seed=0,
            config=M4Config(),
            trace_path=tmp_path / "bad-quality.jsonl",
            relaxation={**relaxation, **changes},
        )
    assert not (tmp_path / "bad-quality.jsonl").exists()


def test_missing_preprocessing_is_not_replaced_by_any_exact_solution(
    reference: FrozenReference, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="preparation did not converge"):
        train_variant(
            reference,
            algorithm="warm_start",
            p=1,
            seed=0,
            config=M4Config(),
            trace_path=tmp_path / "none.jsonl",
        )
    assert not (tmp_path / "none.jsonl").exists()


@pytest.mark.parametrize("algorithm", ["standard", "warm_start"])
def test_existing_trace_is_never_overwritten(
    reference: FrozenReference, relaxation: dict[str, Any], tmp_path: Path, algorithm: str
) -> None:
    path = tmp_path / "existing.jsonl"
    path.write_text("historical data\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Trace already exists"):
        train_variant(
            reference,
            algorithm=algorithm,
            p=1,
            seed=0,
            config=M4Config(),
            trace_path=path,
            relaxation=relaxation,
        )  # type: ignore[arg-type]
    assert path.read_text(encoding="utf-8") == "historical data\n"


def test_failed_operator_gate_blocks_warm_circuit_before_trace(
    reference: FrozenReference,
    table: StateTable,
    relaxation: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    training._full_operator_gate.cache_clear()
    monkeypatch.setattr(training, "verify_operator", lambda *args: {"passed": False})

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("A failed operator gate must block the circuit")

    monkeypatch.setattr(training, "build_warm_qaoa", forbidden)
    try:
        with pytest.raises(ValueError, match="operator gate"):
            train_variant(
                reference,
                algorithm="warm_start",
                p=1,
                seed=0,
                config=M4Config(),
                trace_path=tmp_path / "gate.jsonl",
                relaxation=relaxation,
                table=table,
            )
        assert not (tmp_path / "gate.jsonl").exists()
    finally:
        training._full_operator_gate.cache_clear()


def test_foreign_state_table_and_unplanned_depth_seed_are_rejected(
    reference: FrozenReference, table: StateTable, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="different or unchecked"):
        train_variant(
            reference,
            algorithm="standard",
            p=1,
            seed=0,
            config=M4Config(),
            trace_path=tmp_path / "foreign.jsonl",
            table=replace(table, reference_hash="bad"),
        )
    with pytest.raises(ValueError, match="depth"):
        train_variant(
            reference,
            algorithm="standard",
            p=3,
            seed=0,
            config=M4Config(),
            trace_path=tmp_path / "p3.jsonl",
            table=table,
        )
    with pytest.raises(ValueError, match="seed"):
        train_variant(
            reference,
            algorithm="standard",
            p=1,
            seed=5,
            config=M4Config(),
            trace_path=tmp_path / "seed5.jsonl",
            table=table,
        )


@pytest.mark.parametrize("algorithm", ["standard", "warm_start"])
def test_empty_reference_is_parameter_free_with_only_one_final_verification(
    tmp_path: Path, algorithm: str
) -> None:
    instance = SchedulingInstance(instance_id="m4-empty", seed=0, workers=(), jobs=())
    qubo = encode_qubo(instance)
    ising = qubo_to_ising(qubo)
    reference = FrozenReference(
        name="m4-empty",
        instance=instance,
        qubo=qubo,
        ising=ising,
        ground_bits=("",),
        ground_energy=0,
        constraint_energy_margin=None,
        reference_hash="m4-empty",
        qubo_hash=canonical_hash(qubo),
        ising_hash=canonical_hash(ising),
    )
    run, _, probabilities = train_variant(
        reference,
        algorithm=algorithm,
        p=1,
        seed=0,
        config=M4Config(),
        trace_path=tmp_path / "empty.jsonl",
        relaxation=solve_box_relaxation(qubo),
    )  # type: ignore[arg-type]
    assert run["parameter_count"] == run["nfev"] == 0
    assert run["total_expectation_evaluations"] == run["verification_evaluations"] == 1
    assert run["final_parameters"] == run["initial_parameters"] == []
    assert run["final_expected_energy"] == 0
    np.testing.assert_array_equal(probabilities, [1])
