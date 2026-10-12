"""M5B regressions: modern limits, honest unknown legacy fields, named secrets."""

import copy
import hashlib
import io
import platform
import random
import string
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from km_quantum.m4.io import canonical_hash
from km_quantum.m5.provider import (
    IBMRuntimeAdapter,
    LocalProviderConfig,
    SafetyError,
    preflight,
    safe_runtime_options,
    validate_preflight,
)
from km_quantum.m5.provider_limits import (
    LOCAL_MAX_PAYLOAD_BYTES,
    LimitContractError,
    assess_plan_limits,
    count_sampler_executions,
    service_limit_policy,
    validate_limit_assessment,
)


@pytest.fixture(autouse=True)
def deny_provider_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    def deny(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Contract regressions must never call a provider")

    monkeypatch.setattr(requests.sessions.Session, "request", deny)
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "release", lambda: "synthetic-test-release")


def _metadata() -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "provider_verified": True,
        "source_kind": "ibm_hardware",
        "backend": "synthetic",
        "instance_binding": "a" * 64,
        "plan": "open",
        "pricing_type": "free",
        "cost_eur": 0,
        "operational": True,
        "status_message": "active",
        "simulator": False,
        "fake": False,
        "single_job_contract": True,
        "runtime_version": "0.47.0",
        "obtained_at": now,
        "region": "us-east",
        "usage": {
            "remaining_seconds": 600,
            "consumed_seconds": 0,
            "limit_seconds": 600,
            "observed_at": now,
            "usage_limit_reached": False,
        },
        "limits": {"max_jobs": None, "max_shots": None, "max_pubs": None},
        "service_limits": service_limit_policy(),
        "pending_jobs": {
            "status": "VERIFIED",
            "complete": True,
            "count": 0,
            "observed_at": now,
            "source": "SDK jobs(instance=explicit)",
        },
    }


def test_missing_obsolete_configuration_fields_are_not_missing_owner_configuration() -> None:
    config = LocalProviderConfig(
        instance="crn:v1:fixture", backend="synthetic", account_name="named"
    )

    class Adapter:
        source_kind = "ibm_hardware"
        single_job_contract = True

        def metadata(self, config: Any, plan: Any) -> dict[str, Any]:
            return _metadata()

    result = preflight(config, Adapter())  # type: ignore[arg-type]
    assert result["status"] == "PROVIDER_PREFLIGHT_READY"
    assert result["limits"]["max_jobs"] is None


def test_named_account_bare_sdk_token_is_redacted_without_credential_dump() -> None:
    config = LocalProviderConfig(
        instance="crn:v1:fixture", backend="synthetic", account_name="named"
    )
    adapter = IBMRuntimeAdapter(config)

    def fail() -> Any:
        raise RuntimeError("opaque-stored-key-must-not-escape")

    adapter._service = SimpleNamespace(
        _account=SimpleNamespace(token="opaque-stored-key-must-not-escape"),
        instances=fail,
    )
    adapter._backend = object()
    result = preflight(config, adapter)
    assert "opaque-stored-key-must-not-escape" not in str(result)


def test_exhausted_account_is_blocked_even_with_positive_instance_difference() -> None:
    evidence = _metadata()
    evidence["limits"] = {"max_jobs": 3, "max_shots": 100000, "max_pubs": 300}
    evidence["usage"]["usage_limit_reached"] = True
    with pytest.raises(SafetyError):
        validate_preflight(evidence)


def _native_plan() -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    from qiskit import ClassicalRegister, QuantumCircuit, qpy

    circuit = QuantumCircuit(5)
    circuit.add_register(ClassicalRegister(2, "m2"))
    circuit.x(0)
    circuit.rz(0.25, 1)
    circuit.sx(1)
    circuit.cx(0, 1)
    circuit.measure([0, 1], circuit.cregs[0])
    raw = io.BytesIO()
    qpy.dump(circuit, raw)
    digest = hashlib.sha256(raw.getvalue()).hexdigest()
    plan = {
        "backend": "synthetic",
        "instance_binding": "a" * 64,
        "layout": [0, 1],
        "target_snapshot_sha256": "e" * 64,
        "runtime_options": safe_runtime_options(rep_delay=0.00025),
        "budget": {
            "max_provider_jobs": 3,
            "max_total_shots": 49664,
            "max_qpu_seconds_per_job": 40,
            "max_total_qpu_seconds": 120,
            "max_cost_eur": 0.0,
        },
        "circuits": {"c0": {"qpy_sha256": digest, "num_bits": 2, "mapping": {"provider_width": 5}}},
        "jobs": [
            {
                "id": "smoke",
                "kind": "smoke",
                "max_execution_time": 40,
                "pubs": [
                    {
                        "pub_id": "pub0",
                        "circuit_id": "c0",
                        "circuit_hash": digest,
                        "shots": 128,
                        "num_bits": 2,
                        "block": 0,
                        "case_name": "fixture",
                    }
                ],
            }
        ],
    }
    return plan, {"c0": circuit}, {"c0": digest}


def _attach(plan: dict[str, Any], assessment: dict[str, Any]) -> None:
    plan["provider_limit_assessment"] = assessment
    plan["provider_limit_assessment_sha256"] = assessment["content_sha256"]


def test_real_sampler_broadcasting_counts_executions_after_expansion() -> None:
    from qiskit import QuantumCircuit
    from qiskit.circuit import Parameter

    circuit = QuantumCircuit(1, 1)
    circuit.rz(Parameter("angle"), 0)
    circuit.measure(0, 0)
    assert count_sampler_executions([(circuit, [[0.1], [0.2], [0.3]], 1024)]) == {
        "pub_count": 1,
        "broadcasted_circuits": 3,
        "executions": 3072,
        "pub_shapes": [[3]],
    }
    with pytest.raises(LimitContractError, match="explicit"):
        count_sampler_executions([circuit.assign_parameters([0.1])])


def test_native_assessment_seals_real_sdk_payload_and_conservative_controls() -> None:
    plan, circuits, hashes = _native_plan()
    original = copy.deepcopy(circuits["c0"].metadata)
    assessment = assess_plan_limits(plan, circuits, qpy_hashes=hashes)
    _attach(plan, assessment)
    validate_limit_assessment(plan, service_limit_policy())
    row = assessment["jobs"]["smoke"]
    assert row["executions"] == 128
    assert row["pub_shapes"] == [[]]
    assert row["pubs"][0]["native_gate_counts"] == {"x": 1, "rz": 1, "sx": 1, "cx": 1, "measure": 2}
    assert row["per_qubit_low_level_instructions"]["0"] == (2 + 5 + 10 + 50) * 128
    assert row["per_qubit_low_level_instructions"]["4"] == 50 * 128
    assert 0 < row["payload_bytes"] < row["guarded_payload_bytes"] < LOCAL_MAX_PAYLOAD_BYTES
    assert circuits["c0"].metadata == original
    assert assessment["provider_contacted"] is False
    assert assessment["hardware_used"] is False
    assert service_limit_policy()["payload"]["provider_max_bytes"] is None
    assert service_limit_policy()["campaign_total_jobs"]["value"] is None


@pytest.mark.parametrize("change", ["options", "ordering", "shots", "qpy"])
def test_limit_assessment_rejects_changed_frozen_submission(change: str) -> None:
    plan, circuits, hashes = _native_plan()
    _attach(plan, assess_plan_limits(plan, circuits, qpy_hashes=hashes))
    if change == "options":
        plan["runtime_options"]["execution"]["rep_delay"] *= 2
    elif change == "ordering":
        plan["jobs"][0]["pubs"][0]["pub_id"] = "new-persistent-pub"
    elif change == "shots":
        plan["jobs"][0]["pubs"][0]["shots"] = 1024
    else:
        plan["circuits"]["c0"]["qpy_sha256"] = "b" * 64
    with pytest.raises(LimitContractError):
        validate_limit_assessment(plan, service_limit_policy())


def test_honest_unknown_control_weight_blocks_contract_without_owner_auth() -> None:
    plan, circuits, hashes = _native_plan()
    circuits["c0"].h(0)
    assessment = assess_plan_limits(plan, circuits, qpy_hashes=hashes)
    assert assessment["status"] == "BLOCKED"
    assert assessment["jobs"]["smoke"]["missing"] == ["Undocumented native control weight: h"]


def test_sampler_execution_and_per_qubit_controls_remain_strict() -> None:
    plan, circuits, hashes = _native_plan()
    plan["jobs"][0]["pubs"][0]["shots"] = 10_000_001
    row = assess_plan_limits(plan, circuits, qpy_hashes=hashes)["jobs"]["smoke"]
    assert row["status"] == "BLOCKED"
    assert any("executions" in reason for reason in row["missing"])
    assert any("per-qubit" in reason for reason in row["missing"])


def test_no_parameter_expansion_or_changed_qpy_binding_can_be_hidden() -> None:
    from qiskit.circuit import Parameter

    plan, circuits, hashes = _native_plan()
    with pytest.raises(LimitContractError, match="QPY hash"):
        assess_plan_limits(plan, circuits, qpy_hashes={"c0": "b" * 64})
    circuits["c0"].rz(Parameter("new_angle"), 0)
    with pytest.raises(LimitContractError, match="parameter expansion"):
        assess_plan_limits(plan, circuits, qpy_hashes=hashes)


def test_concrete_serialized_metadata_is_subject_to_local_payload_guard() -> None:
    plan, circuits, hashes = _native_plan()
    rng = random.Random(20261012)
    circuits["c0"].metadata = {
        "synthetic_payload": "".join(rng.choices(string.ascii_letters, k=1_300_000))
    }
    row = assess_plan_limits(plan, circuits, qpy_hashes=hashes)["jobs"]["smoke"]
    assert row["guarded_payload_bytes"] > LOCAL_MAX_PAYLOAD_BYTES
    assert row["status"] == "BLOCKED"
    assert any("1 MiB local guard" in reason for reason in row["missing"])


def test_resealed_negative_or_changed_native_pub_metrics_are_rejected() -> None:
    plan, circuits, hashes = _native_plan()
    assessment = assess_plan_limits(plan, circuits, qpy_hashes=hashes)
    assessment["jobs"]["smoke"]["pubs"][0]["native_gate_counts"]["sx"] = -1
    assessment["content_sha256"] = canonical_hash(
        {k: v for k, v in assessment.items() if k != "content_sha256"}
    )
    _attach(plan, assessment)
    with pytest.raises(LimitContractError, match="Native circuit"):
        validate_limit_assessment(plan, service_limit_policy())


@pytest.mark.parametrize("gate,limit", [("rz", 30_000_000), ("sx", 20_000_000), ("two", 5_000_000)])
def test_documented_native_gate_ceilings_are_strict_even_in_resealed_metrics(
    gate: str, limit: int
) -> None:
    plan, circuits, hashes = _native_plan()
    assessment = assess_plan_limits(plan, circuits, qpy_hashes=hashes)
    pub = assessment["jobs"]["smoke"]["pubs"][0]
    if gate == "two":
        pub["two_qubit_gates"] = limit + 1
    else:
        pub["native_gate_counts"][gate] = limit + 1
    assessment["content_sha256"] = canonical_hash(
        {key: value for key, value in assessment.items() if key != "content_sha256"}
    )
    _attach(plan, assessment)
    with pytest.raises(LimitContractError, match="Native circuit"):
        validate_limit_assessment(plan, service_limit_policy())


@pytest.mark.parametrize("fact", ["pending", "incomplete", "stale", "unknown_usage_flag"])
def test_account_admission_and_reservation_facts_stay_mandatory(fact: str) -> None:
    evidence = _metadata()
    if fact == "pending":
        evidence["pending_jobs"]["count"] = 1
    elif fact == "incomplete":
        evidence["pending_jobs"]["complete"] = False
    elif fact == "stale":
        evidence["pending_jobs"]["observed_at"] = "2020-01-01T00:00:00Z"
    else:
        evidence["usage"].pop("usage_limit_reached")
    with pytest.raises(SafetyError):
        validate_preflight(evidence)


def test_local_contract_failure_preserves_live_facts_and_typed_diagnostic() -> None:
    config = LocalProviderConfig(
        instance="crn:v1:fixture", backend="synthetic", account_name="named"
    )

    class Adapter:
        source_kind = "ibm_hardware"
        single_job_contract = True

        def metadata(self, config: Any, plan: Any) -> dict[str, Any]:
            data = _metadata()
            data["service_limits"]["max_executions_per_job"] = None
            return data

    result = preflight(config, Adapter())  # type: ignore[arg-type]
    assert result["status"] == "BLOCKED"
    assert result["diagnostic_kind"] == "PROVIDER_CONTRACT_ERROR"
    assert result["provider_verified"] is True
    assert result["usage"]["remaining_seconds"] == 600
    assert result["limits"]["max_jobs"] is None


def _mock_live_adapter(states: list[str]) -> tuple[IBMRuntimeAdapter, list[dict[str, Any]]]:
    config = LocalProviderConfig(
        instance="crn:v1:bluemix:public:quantum:us-east:a:fixture:instance::",
        backend="synthetic",
        account_name="named",
    )
    calls: list[dict[str, Any]] = []

    def jobs(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return [SimpleNamespace(status=lambda status=state: status) for state in states]

    adapter = IBMRuntimeAdapter(config)
    adapter._service = SimpleNamespace(
        instances=lambda: [{"crn": config.instance, "plan": "open", "pricing_type": "free"}],
        usage=lambda: {
            "usage_limit_seconds": 600,
            "usage_consumed_seconds": 0,
            "usage_remaining_seconds": 600,
            "usage_limit_reached": False,
        },
        active_instance=lambda: config.instance,
        jobs=jobs,
    )
    adapter._backend = SimpleNamespace(
        configuration=lambda: SimpleNamespace(simulator=False),
        status=lambda: SimpleNamespace(operational=True, status_msg="active"),
        properties=lambda: SimpleNamespace(last_update_date=datetime.now(UTC)),
    )
    return adapter, calls


def test_unfiltered_bounded_inventory_sees_initializing_and_preserves_quota() -> None:
    adapter, calls = _mock_live_adapter(["DONE", "INITIALIZING"])
    result = preflight(adapter.config, adapter)
    assert calls == [{"limit": 101, "pending": None, "instance": adapter.config.instance}]
    assert result["pending_jobs"]["complete"] is True
    assert result["pending_jobs"]["count"] == 1
    assert result["pending_jobs"]["reservation_seconds"] is None
    assert result["status"] == "OWNER_CONFIGURATION_REQUIRED"
    assert result["usage"]["remaining_seconds"] == 600


@pytest.mark.parametrize("states", [["NOT_EXPOSED"], ["DONE"] * 101])
def test_truncated_or_unknown_job_inventory_cannot_claim_complete_reservations(
    states: list[str],
) -> None:
    adapter, _ = _mock_live_adapter(states)
    result = preflight(adapter.config, adapter)
    assert result["pending_jobs"]["complete"] is False
    assert result["pending_jobs"]["count"] is None
    assert result["status"] == "OWNER_CONFIGURATION_REQUIRED"


def test_no_obsolete_backend_fields_is_ready_after_known_empty_job_inventory() -> None:
    adapter, _ = _mock_live_adapter([])
    result = preflight(adapter.config, adapter)
    assert result["status"] == "PROVIDER_PREFLIGHT_READY"
    assert result["region"] == "us-east"
    assert result["limits"] == {"max_jobs": None, "max_shots": None, "max_pubs": None}


def _local_model(
    plan: dict[str, Any], circuits: dict[str, Any], hashes: dict[str, str]
) -> dict[str, Any]:
    from qiskit.circuit import Measure, Parameter, Reset
    from qiskit.circuit.library import CXGate, RZGate, SXGate, XGate
    from qiskit.transpiler import InstructionProperties, Target

    from km_quantum.m5.usage import estimate_plan_usage

    target = Target(num_qubits=5, dt=1e-9)
    for gate in (XGate(), SXGate(), RZGate(Parameter("angle")), Reset(), Measure()):
        target.add_instruction(gate, {(q,): InstructionProperties(duration=1e-6) for q in range(5)})
    target.add_instruction(CXGate(), {(0, 1): InstructionProperties(duration=1e-6)})
    plan["target_snapshot_sha256"] = "e" * 64
    model = estimate_plan_usage(
        plan,
        circuits,
        target,
        rep_delay={
            "value": 0.00025,
            "range": [0, 0.002],
            "supported": True,
            "source": "synthetic target default",
        },
        target_snapshot_sha256=plan["target_snapshot_sha256"],
        qpy_hashes=hashes,
    )
    plan["local_usage_model"] = model
    plan["usage_model_sha256"] = model["model_sha256"]
    for job in plan["jobs"]:
        job["estimated_qpu_seconds"] = model["execution_estimates"][job["id"]]["seconds"]
        job["estimate_source"] = model["execution_estimates"][job["id"]]["source"]
    return model


def test_preflight_requires_complete_bound_local_model_and_retains_cap_difference() -> None:
    plan, circuits, hashes = _native_plan()
    _attach(plan, assess_plan_limits(plan, circuits, qpy_hashes=hashes))
    evidence = _metadata()
    with pytest.raises(SafetyError) as error:
        validate_preflight(evidence, plan)
    assert error.value.diagnostic_kind == "INSUFFICIENT_USAGE_MODEL"
    model = _local_model(plan, circuits, hashes)
    evidence["local_usage_model"] = model
    evidence["execution_estimates"] = model["execution_estimates"]
    validate_preflight(evidence, plan)
    assert model["is_provider_quote"] is False
    plan["runtime_options"]["execution"]["rep_delay"] = 0.0005
    _attach(plan, assess_plan_limits(plan, circuits, qpy_hashes=hashes))
    with pytest.raises(SafetyError) as error:
        validate_preflight(evidence, plan)
    assert error.value.diagnostic_kind == "INSUFFICIENT_USAGE_MODEL"


def test_default_partition_over_cap_is_usage_limit_not_owner_credentials() -> None:
    plan, circuits, hashes = _native_plan()
    base = plan["jobs"][0]["pubs"][0]
    plan["jobs"][0]["pubs"] = [{**base, "pub_id": f"pub-{i}"} for i in range(24)]
    _attach(plan, assess_plan_limits(plan, circuits, qpy_hashes=hashes))
    model = _local_model(plan, circuits, hashes)
    evidence = _metadata()
    evidence["local_usage_model"] = model
    evidence["execution_estimates"] = model["execution_estimates"]
    with pytest.raises(SafetyError) as error:
        validate_preflight(evidence, plan)
    assert error.value.diagnostic_kind == "USAGE_LIMIT_EXCEEDED"
