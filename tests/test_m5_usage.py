"""Regression checks for a local model, never a provider timing quotation."""

from __future__ import annotations

import copy
import math

import pytest
from qiskit import QuantumCircuit
from qiskit.circuit import Measure, Parameter, Reset
from qiskit.circuit.library import CXGate, RZGate, XGate
from qiskit.transpiler import InstructionProperties, Target

from km_quantum.m5.io import canonical_hash
from km_quantum.m5.usage import (
    UsagePolicy,
    estimate_job_usage,
    estimate_plan_usage,
    validate_usage_model,
)


def timing_target(*, missing: str | None = None) -> Target:
    target = Target(num_qubits=3, dt=1e-9)
    for operation, duration in (
        (XGate(), 100e-9),
        (RZGate(Parameter("theta")), 0.0),
        (Measure(), 500e-9),
        (Reset(), 700e-9),
    ):
        target.add_instruction(
            operation,
            {
                (q,): InstructionProperties(
                    duration=None if operation.name == missing else duration
                )
                for q in range(3)
            },
        )
    target.add_instruction(CXGate(), {(0, 2): InstructionProperties(duration=300e-9)})
    return target


def circuit(*, twice: bool = False, explicit_reset: bool = False) -> QuantumCircuit:
    result = QuantumCircuit(3, 2)
    if explicit_reset:
        result.reset(0)
    result.x(0)
    if twice:
        result.x(0)
    result.cx(0, 2)
    result.measure(0, 0)
    result.measure(2, 1)
    return result


def pub(identity: str, shots: int) -> dict:
    return {
        "pub_id": f"pub-{identity}",
        "circuit_id": identity,
        "circuit_hash": "a" * 64,
        "shots": shots,
    }


def estimate(job: dict, circuits: dict, target: Target | None = None, **kwargs: object) -> dict:
    return estimate_job_usage(
        job,
        circuits,
        target or timing_target(),
        rep_delay={
            "supported": True,
            "value": 250e-6,
            "range": [0, 0.002],
            "source": "synthetic explicit backend default",
        },
        target_snapshot_sha256="b" * 64,
        qpy_hashes=dict.fromkeys(circuits, "a" * 64),
        **kwargs,
    )


def test_heterogeneous_shots_sum_each_execution_with_seconds_and_separate_readout() -> None:
    job = {"id": "smoke", "max_execution_time": 40, "pubs": [pub("a", 10), pub("b", 100)]}
    result = estimate(job, {"a": circuit(), "b": circuit(twice=True)})
    # Parallel measurements take 500 ns, rather than 2*500 ns. Initialization
    # reset is parallel over the active physical IDs, including sparse ID 2.
    expected = 10 * (900e-9 + 700e-9 + 250e-6) + 100 * (1e-6 + 700e-9 + 250e-6)
    assert result["shot_time_seconds"] == pytest.approx(expected)
    assert result["central_seconds"] == pytest.approx(2 + expected)
    assert result["total_executions"] == 110
    assert result["pubs"][0]["timings"][0]["readout_included_once"] is True
    assert result["pubs"][0]["timings"][0]["initialization_seconds"] == 700e-9
    assert result["pubs"][0]["timings"][0]["active_physical_qubits"] == [0, 2]
    assert result["units"] == "seconds"
    assert result["is_provider_quote"] is False
    assert result["is_guaranteed_upper_bound"] is False


def test_default_rep_delay_is_explicit_and_not_reduced_for_budget() -> None:
    result = estimate({"id": "smoke", "pubs": [pub("a", 1)]}, {"a": circuit()})
    assert result["rep_delay"]["seconds"] == 0.00025
    assert result["rep_delay"]["source"] == "synthetic explicit backend default"
    assert result["initialization_policy"]["enabled"] is True
    assert result["hard_limits"]["max_qpu_seconds_per_job"] == 40
    assert result["hard_limits"]["max_total_qpu_seconds"] == 120


def test_explicit_reset_is_circuit_work_in_addition_to_provider_initialization() -> None:
    result = estimate({"id": "smoke", "pubs": [pub("a", 2)]}, {"a": circuit(explicit_reset=True)})
    timing = result["pubs"][0]["timings"][0]
    assert timing["circuit_seconds"] == pytest.approx(1.6e-6)
    assert timing["initialization_seconds"] == 700e-9
    assert timing["explicit_reset_count"] == 1
    assert result["shot_time_seconds"] == pytest.approx(2 * (1.6e-6 + 700e-9 + 250e-6))


def test_analysis_never_mutates_bound_hardware_or_noise_circuit() -> None:
    original = circuit()
    before = original.copy()
    metadata_before = copy.deepcopy(original.metadata)
    result = estimate({"id": "smoke", "pubs": [pub("a", 10)]}, {"a": original})
    assert original == before
    assert original.metadata == metadata_before
    assert "delay" not in original.count_ops()
    assert result["circuit_hashes"] == {"a": "a" * 64}
    assert len(result["target_durations_sha256"]) == 64
    assert len(result["config_sha256"]) == 64
    assert len(result["model_sha256"]) == 64


def test_parameter_broadcast_counts_actual_bound_executions() -> None:
    theta = Parameter("theta")
    varying = QuantumCircuit(3, 1)
    varying.rz(theta, 0)
    varying.measure(0, 0)
    value = pub("a", 7)
    value["parameter_values"] = [[0.1], [0.2], [0.3]]
    result = estimate({"id": "smoke", "pubs": [value]}, {"a": varying})
    assert result["pub_count"] == 1
    assert result["expanded_circuit_count"] == 3
    assert result["total_executions"] == 21
    assert result["pubs"][0]["broadcast_shape"] == [3]
    assert len(result["pubs"][0]["timings"]) == 3
    assert varying.num_parameters == 1


@pytest.mark.parametrize("missing", ["x", "measure", "reset"])
def test_missing_native_or_reset_duration_blocks_without_zero_fallback(missing: str) -> None:
    result = estimate(
        {"id": "smoke", "pubs": [pub("a", 10)]}, {"a": circuit()}, timing_target(missing=missing)
    )
    assert result["status"] == "BLOCKED"
    assert result["diagnostic_kind"] == "INSUFFICIENT_USAGE_MODEL"
    assert result["central_seconds"] is None
    assert any(missing in entry for entry in result["missing"])


@pytest.mark.parametrize("rep_delay", [None, {}, {"value": 0.00025}, {"supported": False}])
def test_unknown_default_or_rep_delay_source_blocks(rep_delay: object) -> None:
    result = estimate_job_usage(
        {"id": "smoke", "pubs": [pub("a", 10)]},
        {"a": circuit()},
        timing_target(),
        rep_delay=rep_delay,
        target_snapshot_sha256="b" * 64,
        qpy_hashes={"a": "a" * 64},
    )
    assert result["status"] == "BLOCKED"
    assert "rep_delay" in " ".join(result["missing"])


def test_subjob_uncertainty_is_separate_from_documented_nominal_overhead() -> None:
    result = estimate(
        {"id": "main-block-1", "pubs": [pub(str(index), 1) for index in range(24)]},
        {str(index): circuit() for index in range(24)},
    )
    assert result["central_subjobs"] == 1
    assert result["local_policy_subjobs"] == 24
    assert result["local_policy_seconds"] > 40
    assert result["status"] == "BLOCKED"
    assert result["diagnostic_kind"] == "USAGE_LIMIT_EXCEEDED"
    assert result["overhead"]["provider_confirmed_partition"] is False
    assert result["uncertainties"]


def test_explicit_unknown_partition_policy_is_insufficient_model_not_owner_setup() -> None:
    result = estimate(
        {"id": "smoke", "pubs": [pub("a", 10)]},
        {"a": circuit()},
        policy=UsagePolicy(subjob_policy="unknown"),
    )
    assert result["status"] == "BLOCKED"
    assert result["diagnostic_kind"] == "INSUFFICIENT_USAGE_MODEL"
    assert "subjob_partition_policy" in result["missing"]


def test_above_40_seconds_is_blocked_even_when_central_value_fits() -> None:
    result = estimate(
        {"id": "smoke", "pubs": [pub("a", 140000)]},
        {"a": circuit()},
    )
    assert result["central_seconds"] < 40 < result["local_policy_seconds"]
    assert result["status"] == "BLOCKED"
    assert result["hard_limits"]["max_qpu_seconds_per_job"] == 40


def test_job_cap_cannot_be_increased_and_wrong_qpy_binding_blocks() -> None:
    result = estimate(
        {"id": "smoke", "max_execution_time": 41, "pubs": [pub("a", 1)]}, {"a": circuit()}
    )
    assert result["status"] == "BLOCKED"
    assert "max_execution_time" in " ".join(result["missing"])
    mismatched = pub("a", 1)
    mismatched["circuit_hash"] = "c" * 64
    result = estimate({"id": "smoke", "pubs": [mismatched]}, {"a": circuit()})
    assert result["status"] == "BLOCKED"
    assert "circuit_hash" in " ".join(result["missing"])


def test_plan_above_120_seconds_remains_blocked_and_has_compatible_estimate_map() -> None:
    jobs = [{"id": f"job-{index}", "pubs": [pub("a", 150000)]} for index in range(3)]
    plan = {"jobs": jobs, "runtime_options": {"execution": {"init_qubits": True}}}
    result = estimate_plan_usage(
        plan,
        {"a": circuit()},
        timing_target(),
        rep_delay={"supported": True, "value": 250e-6, "range": [0, 0.002], "source": "test"},
        target_snapshot_sha256="b" * 64,
        qpy_hashes={"a": "a" * 64},
    )
    assert result["local_policy_total_seconds"] > 120
    assert result["status"] == "BLOCKED"
    assert result["hard_limits"]["max_total_qpu_seconds"] == 120
    assert result["execution_estimates"]["job-0"]["is_provider_quote"] is False
    assert result["execution_estimates"]["job-0"]["source"] == "m5_local_target_usage_model"


@pytest.mark.parametrize("value", [-1, math.inf, math.nan])
def test_invalid_duration_fails_without_nonfinite_json(value: object) -> None:
    target = timing_target()
    target.update_instruction_properties("x", (0,), InstructionProperties(duration=value))
    result = estimate({"id": "smoke", "pubs": [pub("a", 1)]}, {"a": circuit()}, target)
    assert result["status"] == "BLOCKED"
    assert result["central_seconds"] is None


def plan_and_model() -> tuple[dict, dict]:
    plan = {
        "jobs": [{"id": "smoke", "max_execution_time": 40, "pubs": [pub("a", 10)]}],
        "circuits": {"a": {"qpy_sha256": "a" * 64}},
        "target_snapshot_sha256": "b" * 64,
        "runtime_options": {"execution": {"init_qubits": True, "rep_delay": 250e-6}},
    }
    model = estimate_plan_usage(
        plan,
        {"a": circuit()},
        timing_target(),
        rep_delay={"supported": True, "value": 250e-6, "range": [0, 0.002], "source": "test"},
        target_snapshot_sha256="b" * 64,
        qpy_hashes={"a": "a" * 64},
    )
    return plan, model


def test_complete_model_validation_binds_plan_but_ignores_estimated_output_fields() -> None:
    plan, model = plan_and_model()
    validate_usage_model(model, plan)
    plan["jobs"][0].update(estimated_qpu_seconds=20, estimate_source="output field")
    validate_usage_model(model, plan)


@pytest.mark.parametrize("change", ["shots", "qpy", "target", "rep_delay", "cap", "seal", "map"])
def test_model_validation_rejects_unbound_or_changed_inputs(change: str) -> None:
    plan, model = plan_and_model()
    if change == "shots":
        plan["jobs"][0]["pubs"][0]["shots"] = 11
    elif change == "qpy":
        plan["circuits"]["a"]["qpy_sha256"] = "d" * 64
    elif change == "target":
        plan["target_snapshot_sha256"] = "d" * 64
    elif change == "rep_delay":
        plan["runtime_options"]["execution"]["rep_delay"] = 0.0001
    elif change == "cap":
        plan["jobs"][0]["max_execution_time"] = 41
    elif change == "seal":
        model["jobs"][0]["central_seconds"] = 0
    else:
        model["execution_estimates"]["smoke"]["seconds"] = 0
        model["model_sha256"] = canonical_hash(
            {key: value for key, value in model.items() if key != "model_sha256"}
        )
    with pytest.raises(ValueError):
        validate_usage_model(model, plan)


def test_model_validation_rejects_resealed_inconsistent_local_arithmetic() -> None:
    plan, model = plan_and_model()
    job = model["jobs"][0]
    job["central_seconds"] = 1.0
    job["model_sha256"] = canonical_hash(
        {key: value for key, value in job.items() if key != "model_sha256"}
    )
    model["model_sha256"] = canonical_hash(
        {key: value for key, value in model.items() if key != "model_sha256"}
    )
    with pytest.raises(ValueError, match="arithmetic"):
        validate_usage_model(model, plan)


def test_blocked_model_is_archivable_but_cannot_become_ready_evidence() -> None:
    plan, model = plan_and_model()
    plan["jobs"][0]["pubs"][0]["shots"] = 140000
    model = estimate_plan_usage(
        plan,
        {"a": circuit()},
        timing_target(),
        rep_delay={"supported": True, "value": 250e-6, "range": [0, 0.002], "source": "test"},
        target_snapshot_sha256="b" * 64,
        qpy_hashes={"a": "a" * 64},
    )
    validate_usage_model(model, plan, require_ready=False)
    with pytest.raises(ValueError, match="40-second"):
        validate_usage_model(model, plan)


def test_units_target_dt_changes_binding_without_rescaling_si_durations() -> None:
    first_target, second_target = timing_target(), timing_target()
    second_target.dt = 2e-9
    first = estimate({"id": "smoke", "pubs": [pub("a", 10)]}, {"a": circuit()}, first_target)
    second = estimate({"id": "smoke", "pubs": [pub("a", 10)]}, {"a": circuit()}, second_target)
    assert first["central_seconds"] == second["central_seconds"]
    assert first["target_durations_sha256"] != second["target_durations_sha256"]


def test_explicit_reviewed_synthetic_partition_is_bound_but_never_provider_fact() -> None:
    job = {"id": "smoke", "pubs": [pub(str(index), 1) for index in range(24)]}
    result = estimate(
        job,
        {str(index): circuit() for index in range(24)},
        policy=UsagePolicy(
            subjob_policy="reviewed_partition_scenario",
            max_subjobs=1,
            reviewed_source="synthetic test fixture with one internal partition",
        ),
    )
    assert result["status"] == "READY"
    assert result["local_policy_subjobs"] == 1
    assert result["local_policy"]["is_provider_fact"] is False
    assert result["overhead"]["provider_confirmed_partition"] is False
    assert result["local_policy"]["reviewed_source"].startswith("synthetic test fixture")
    default_result = estimate(job, {str(index): circuit() for index in range(24)})
    assert default_result["status"] == "BLOCKED"
    assert default_result["config_sha256"] != result["config_sha256"]


@pytest.mark.parametrize(
    ("subjobs", "source"),
    [(None, "test"), (1.5, "test"), (0, "test"), (True, "test"), (1, None), (1, " ")],
)
def test_reviewed_partition_requires_integral_count_and_nonempty_review(
    subjobs: object, source: object
) -> None:
    with pytest.raises(ValueError, match="Reviewed partition"):
        UsagePolicy(
            subjob_policy="reviewed_partition_scenario",
            max_subjobs=subjobs,
            reviewed_source=source,
        )
