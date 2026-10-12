"""Advisory-only comparison preserves frozen inputs and submission guards."""

from __future__ import annotations

import copy
import math

import pytest
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister
from qiskit.circuit import Measure, Reset
from qiskit.circuit.library import XGate
from qiskit.transpiler import InstructionProperties, Target
from test_m5_planning import prepared_fixture

from km_quantum.m5.approvals import plan_hash, validate_human_approval, validate_plan
from km_quantum.m5.budget_comparison import (
    compare_budget_variants,
    evaluate_budget,
    summarize_job_time,
    validate_budget_comparison,
)
from km_quantum.m5.io import canonical_hash
from km_quantum.m5.models import M5Config
from km_quantum.m5.planning import make_plan
from km_quantum.m5.provider import SafetyError
from km_quantum.m5.usage import estimate_job_usage, estimate_plan_usage

_SOURCE_HEAD = "5d771d227ddf5036007173f8eac1c45bb46c044f"


def fixture() -> tuple[dict, dict]:
    """Public synthetic timings; no account, runtime service or provider calls."""
    plan = make_plan(prepared_fixture(), M5Config())
    target = Target(num_qubits=16)
    for operation, duration in ((XGate(), 1e-6), (Measure(), 2e-6), (Reset(), 3e-6)):
        target.add_instruction(
            operation,
            {(qubit,): InstructionProperties(duration=duration) for qubit in range(16)},
        )
    circuits = {}
    for index, (identity, row) in enumerate(plan["circuits"].items()):
        circuit = QuantumCircuit(QuantumRegister(16), ClassicalRegister(row["num_bits"], "m2"))
        for _ in range(1 + index % 3):
            circuit.x(plan["layout"][0])
        for bit, physical in enumerate(plan["layout"][: row["num_bits"]]):
            circuit.measure(physical, bit)
        circuits[identity] = circuit
    model = estimate_plan_usage(
        plan,
        circuits,
        target,
        rep_delay={"supported": True, "value": 0.00025, "range": [0, 0.002], "source": "synthetic"},
        target_snapshot_sha256=plan["target_snapshot_sha256"],
        qpy_hashes={identity: row["qpy_sha256"] for identity, row in plan["circuits"].items()},
    )
    plan["local_usage_model"] = copy.deepcopy(model)
    plan["usage_model_sha256"] = model["model_sha256"]
    for job in plan["jobs"]:
        job["estimated_qpu_seconds"] = model["execution_estimates"][job["id"]]["seconds"]
        job["estimate_source"] = "m5_local_target_usage_model"
    plan["plan_hash"] = plan_hash(plan)
    return plan, model


def test_exactly_two_advisory_variants_preserve_all_original_inputs_and_counts() -> None:
    plan, model = fixture()
    before = copy.deepcopy((plan, model))
    comparison = compare_budget_variants(plan, model, source_head=_SOURCE_HEAD)
    assert (plan, model) == before
    assert [variant["variant"] for variant in comparison["variants"]] == ["A", "B"]
    a, b = comparison["variants"]
    assert a["total_executions"] == 25088
    assert b["total_executions"] == 49664
    assert a["total_pubs"] == b["total_pubs"] == 52
    assert a["campaign_job_count"] == b["campaign_job_count"] == 3
    assert [job["total_executions"] for job in a["job_summaries"]] == [512, 12288, 12288]
    assert a["active_limits"]["max_total_qpu_seconds"] == 120
    assert comparison["active_limits"]["max_qpu_seconds_per_job"] == 40
    assert comparison["hardware_enabled"] is False
    assert comparison["submission_approved"] is False
    validate_budget_comparison(comparison, plan, model)


def test_halving_main_shots_keeps_fixed_overhead_and_scales_only_shot_duration() -> None:
    plan, model = fixture()
    comparison = compare_budget_variants(plan, model, source_head=_SOURCE_HEAD)
    original = comparison["original_summary"]["job_summaries"][1]
    variant = comparison["variants"][0]["job_summaries"][1]
    assert variant["shot_time_seconds"] == pytest.approx(original["shot_time_seconds"] / 2)
    assert variant["risk_fixed_overhead_seconds"] == 48
    assert variant["risk_overhead_margin_seconds"] == 12
    assert variant["risk_seconds"] == pytest.approx(60 + 1.25 * original["shot_time_seconds"] / 2)
    assert variant["risk_seconds"] != pytest.approx(original["risk_seconds"] / 2)
    assert variant["theoretical_zero_shot_risk_seconds"] == 60
    assert variant["fixed_risk_alone_exceeds_active_job_cap"] is True
    assert comparison["theoretical_zero_shot_campaign_risk_seconds"] == 130
    assert comparison["theoretical_zero_shot_is_valid_hardware_plan"] is False


def test_risk_policy_remains_local_and_both_active_limit_exceedances_are_explicit() -> None:
    plan, model = fixture()
    comparison = compare_budget_variants(plan, model, source_head=_SOURCE_HEAD)
    a = comparison["variants"][0]
    assert a["status"] == "BLOCKED"
    assert a["diagnostic_kind"] == "LOCAL_CAMPAIGN_LIMIT_EXCEEDED"
    assert a["budget_evaluation"]["within_total_cap"] is False
    assert a["budget_evaluation"]["job_details"]["main-block-1"]["within_cap"] is False
    assert comparison["is_provider_quote"] is False
    assert comparison["is_guaranteed_upper_bound"] is False
    assert comparison["risk_policy"]["is_provider_fact"] is False


@pytest.mark.parametrize(
    ("seconds", "total_cap", "expected"),
    [
        ({"one": 39.0, "two": 39.0}, 120, True),
        ({"one": 41.0, "two": 39.0}, 120, False),
        ({"one": 40.0, "two": 40.0}, 79, False),
        ({"one": 40.0, "two": 40.0}, 80, True),
    ],
)
def test_budget_evaluation_covers_under_over_and_equal_limits(
    seconds: dict, total_cap: int, expected: bool
) -> None:
    result = evaluate_budget(seconds, dict.fromkeys(seconds, 40), total_cap_seconds=total_cap)
    assert result["fits_all_limits"] is expected
    assert result["total_seconds"] == sum(seconds.values())


def test_b_proposed_budget_stays_non_executable_and_cannot_obtain_human_approval() -> None:
    plan, model = fixture()
    comparison = compare_budget_variants(plan, model, source_head=_SOURCE_HEAD)
    b = comparison["variants"][1]
    assert b["status"] == "OWNER_BUDGET_APPROVAL_REQUIRED"
    assert b["proposed_reservations"]["per_job_seconds"] == {
        "smoke": 20,
        "main-block-1": 80,
        "main-block-2": 80,
    }
    assert b["proposed_reservations"]["campaign_seconds"] == 180
    assert b["budget_evaluation"]["fits_all_limits"] is True
    for artifact in (comparison, b):
        assert "jobs" not in artifact
        assert "runtime_options" not in artifact
        assert "plan_hash" not in artifact
        assert "approval_request" not in artifact
        with pytest.raises(SafetyError, match="concrete verified"):
            validate_plan(artifact)
        with pytest.raises(SafetyError, match="concrete verified"):
            validate_human_approval(artifact, {"approved": True, "signed_by": "Florian"})


def test_heterogeneous_shots_readout_and_reset_count_once_in_duration_sum() -> None:
    target = Target(num_qubits=1)
    for operation, duration in ((XGate(), 1e-6), (Measure(), 2e-6), (Reset(), 3e-6)):
        target.add_instruction(operation, {(0,): InstructionProperties(duration=duration)})
    first, second = QuantumCircuit(1, 1), QuantumCircuit(1, 1)
    first.x(0)
    first.measure(0, 0)
    second.x(0)
    second.x(0)
    second.measure(0, 0)
    job = {
        "id": "heterogeneous",
        "pubs": [
            {"pub_id": "p1", "circuit_id": "first", "circuit_hash": "a" * 64, "shots": 10},
            {"pub_id": "p2", "circuit_id": "second", "circuit_hash": "b" * 64, "shots": 100},
        ],
    }
    model = estimate_job_usage(
        job,
        {"first": first, "second": second},
        target,
        rep_delay={"supported": True, "value": 250e-6, "range": [0, 0.002], "source": "synthetic"},
        target_snapshot_sha256="c" * 64,
        qpy_hashes={"first": "a" * 64, "second": "b" * 64},
    )
    summary = summarize_job_time(model)
    assert summary["total_executions"] == 110
    assert summary["native_circuit_including_readout_seconds"] == pytest.approx(
        10 * 3e-6 + 100 * 4e-6
    )
    assert summary["initialization_seconds"] == pytest.approx(110 * 3e-6)
    assert summary["rep_delay_seconds"] == pytest.approx(110 * 250e-6)
    assert summary["shot_time_seconds"] == pytest.approx(
        10 * (3e-6 + 3e-6 + 250e-6) + 100 * (4e-6 + 3e-6 + 250e-6)
    )
    assert summary["risk_overhead_margin_seconds"] == 1
    assert summary["risk_shot_margin_seconds"] == pytest.approx(0.25 * summary["shot_time_seconds"])


@pytest.mark.parametrize("tamper", ["plan", "model", "model_binding", "qpy", "draft", "source"])
def test_seals_and_source_inventory_bindings_reject_changes(tamper: str) -> None:
    plan, model = fixture()
    comparison = compare_budget_variants(plan, model, source_head=_SOURCE_HEAD)
    if tamper == "plan":
        plan["jobs"][1]["pubs"][0]["shots"] = 512
    elif tamper == "model":
        model["jobs"][1]["central_seconds"] = 1
    elif tamper == "model_binding":
        plan["usage_model_sha256"] = "d" * 64
        plan["plan_hash"] = plan_hash(plan)
    elif tamper == "qpy":
        first = next(iter(plan["circuits"]))
        plan["circuits"][first]["qpy_sha256"] = "d" * 64
        plan["plan_hash"] = plan_hash(plan)
    elif tamper == "draft":
        comparison["variants"][1]["proposed_reservations"]["campaign_seconds"] = 181
        comparison["draft_sha256"] = canonical_hash(
            {key: value for key, value in comparison.items() if key != "draft_sha256"}
        )
    else:
        comparison["source_bindings"]["source_head"] = "f" * 40
    with pytest.raises((ValueError, SafetyError)):
        validate_budget_comparison(comparison, plan, model)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -1, True])
def test_budget_evaluation_rejects_invalid_times(bad: object) -> None:
    with pytest.raises(ValueError):
        evaluate_budget({"job": bad}, {"job": 40}, total_cap_seconds=120)
