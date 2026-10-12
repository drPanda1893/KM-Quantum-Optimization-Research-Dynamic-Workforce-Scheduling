"""Budget B is explicit; hardware approval and serial reservation remain separate."""

from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister, qpy
from qiskit.circuit import Measure, Reset
from qiskit.circuit.library import XGate
from qiskit.transpiler import InstructionProperties, Target
from test_m5_approval import NOW, synthetic_approval, synthetic_plan, synthetic_preflight
from test_m5_ledger import SyntheticAdapter, SyntheticJob

from km_quantum.m5.approvals import (
    approval_request,
    plan_hash,
    validate_approval,
    validate_human_approval,
    validate_plan,
)
from km_quantum.m5.budget_policy import (
    BUDGET_B_SOURCE_REQUEST_SHA256,
    BUDGET_B_VERSION,
    JOB_IDS,
    approved_budget_b_payload,
    resolve_budget_policy,
)
from km_quantum.m5.io import canonical_hash
from km_quantum.m5.ledger import JobLedger
from km_quantum.m5.models import M5Config
from km_quantum.m5.provider import (
    IBMRuntimeAdapter,
    LocalProviderConfig,
    SafetyError,
    plan_budget_policy,
    redact,
    runtime_options_for_job,
    safe_runtime_options,
    sdk_option_contract,
    validate_preflight,
)
from km_quantum.m5.provider_limits import (
    assess_plan_limits,
    plan_payload_hash,
    service_limit_policy,
    validate_limit_assessment,
)
from km_quantum.m5.usage import UsagePolicy, estimate_plan_usage


def budget_authorization_fixture() -> dict[str, Any]:
    """Synthetic budget-only record; never hardware authorization."""
    payload = {
        "artifact_type": "m5_budget_authorization",
        "schema_version": 1,
        "budget_policy_version": BUDGET_B_VERSION,
        "scope": "budget_only",
        "approved": True,
        "authorized_by": "Florian",
        "authorized_at": NOW,
        "source_head": "5c186536d9b383d66d7a209f2835ccf3748e9b3e",
        "source_request_sha256": BUDGET_B_SOURCE_REQUEST_SHA256,
        "approved_budget": approved_budget_b_payload(),
        "hardware_enabled": False,
        "submission_approved": False,
        "hardware_submission_authorized": False,
    }
    return {**payload, "content_sha256": canonical_hash(payload)}


def _circuit() -> QuantumCircuit:
    circuit = QuantumCircuit(QuantumRegister(16, "q"), ClassicalRegister(3, "m2"))
    circuit.x(9)
    circuit.measure([9, 3, 6], circuit.cregs[0])
    return circuit


def budget_b_plan(circuit_hash: str = "a" * 64) -> dict[str, Any]:
    """Complete sealed *synthetic* model with an explicitly reviewed partition."""
    plan = synthetic_plan(circuit_hash)
    proof = budget_authorization_fixture()
    policy = resolve_budget_policy(BUDGET_B_VERSION, proof)
    plan.update(budget_policy_version=BUDGET_B_VERSION, budget_authorization=proof)
    plan["budget"].update(
        max_qpu_seconds_per_job=80,
        max_total_qpu_seconds=180,
        per_job_qpu_seconds=policy.per_job_qpu_seconds,
        budget_authorization_sha256=proof["content_sha256"],
    )
    plan["runtime_options"] = safe_runtime_options(80, rep_delay=0.00025, budget_policy=policy)
    plan["job_runtime_options"] = {
        identity: {**plan["runtime_options"], "max_execution_time": policy.cap_for_job(identity)}
        for identity in JOB_IDS
    }
    for job, identity in zip(plan["jobs"], JOB_IDS, strict=True):
        job.update(id=identity, max_execution_time=policy.cap_for_job(identity))
    target = Target(num_qubits=16)
    properties = {(qubit,): InstructionProperties(duration=1e-6, error=0) for qubit in range(16)}
    for operation in (XGate(), Measure(), Reset()):
        target.add_instruction(operation, properties.copy())
    config = M5Config(
        budget_policy_version=BUDGET_B_VERSION,
        budget_authorization=proof,
        max_qpu_seconds_per_job=80,
        max_total_qpu_seconds=180,
    )
    circuits = {identity: _circuit() for identity in plan["circuits"]}
    hashes = {identity: row["qpy_sha256"] for identity, row in plan["circuits"].items()}
    model = estimate_plan_usage(
        plan,
        circuits,
        target,
        rep_delay={
            "supported": True,
            "value": 0.00025,
            "range": [0, 0.001],
            "source": "synthetic default",
        },
        target_snapshot_sha256=plan["target_snapshot_sha256"],
        qpy_hashes=hashes,
        config=config,
        policy=UsagePolicy(
            subjob_policy="reviewed_partition_scenario",
            max_subjobs=1,
            reviewed_source="synthetic unit test; no provider partition guarantee",
        ),
    )
    plan.update(local_usage_model=model, usage_model_sha256=model["model_sha256"])
    assessment = assess_plan_limits(plan, circuits, qpy_hashes=hashes)
    plan.update(
        provider_limit_assessment=assessment,
        provider_limit_assessment_sha256=assessment["content_sha256"],
    )
    for job in plan["jobs"]:
        estimate = model["execution_estimates"][job["id"]]
        job.update(estimated_qpu_seconds=estimate["seconds"], estimate_source=estimate["source"])
    return plan


def _evidence(plan: dict[str, Any]) -> dict[str, Any]:
    evidence = synthetic_preflight(plan)
    evidence["usage"].update(remaining_seconds=600, consumed_seconds=0, limit_seconds=600)
    return evidence


class BudgetBAdapter(SyntheticAdapter):
    def submit(self, pubs: Any, options: Any, tags: Any) -> SyntheticJob:
        state = self.ledger.snapshot()
        assert state["jobs"][-1]["status"] == "SUBMISSION_UNKNOWN"
        assert state["reserved_qpu_seconds"] == sum((20, 80, 80)[: len(self.calls) + 1])
        assert options["max_execution_time"] == (20, 80, 80)[len(self.calls)]
        self.calls.append((pubs, copy.deepcopy(options), tags))
        job = SyntheticJob(f"synthetic-b-{len(self.calls)}", pubs, status=self.current_status)
        self.jobs[job.identity] = job
        if self.raise_after_accept:
            raise TimeoutError("Unknown acceptance; Authorization: Bearer private-test-token")
        return job


@pytest.fixture
def experiment_b(
    tmp_path: Path,
) -> tuple[dict[str, Any], dict[str, Path], JobLedger, BudgetBAdapter]:
    raw = io.BytesIO()
    qpy.dump(_circuit(), raw)
    path = tmp_path / "provider.qpy"
    path.write_bytes(raw.getvalue())
    plan = budget_b_plan(hashlib.sha256(raw.getvalue()).hexdigest())
    ledger = JobLedger(tmp_path / "ledger.json")
    return plan, {identity: path for identity in plan["circuits"]}, ledger, BudgetBAdapter(ledger)


def _submit_b(experiment: Any) -> dict[str, Any]:
    plan, paths, ledger, adapter = experiment
    return ledger.submit_next(
        plan, synthetic_approval(plan), _evidence(plan), adapter, paths, now=NOW
    )


def test_explicit_b_policy_sdk_contract_and_v1_default() -> None:
    policy = resolve_budget_policy(BUDGET_B_VERSION, budget_authorization_fixture())
    for cap in (20, 80):
        assert (
            sdk_option_contract(
                safe_runtime_options(cap, budget_policy=policy), budget_policy=policy
            )["status"]
            == "PASS"
        )
    assert safe_runtime_options()["max_execution_time"] == 40
    with pytest.raises(SafetyError):
        safe_runtime_options(80)
    with pytest.raises(SafetyError):
        sdk_option_contract(safe_runtime_options(80, budget_policy=policy))


@pytest.mark.parametrize("cap", [False, 0, -1, 81, 80.0])
def test_b_runtime_cap_remains_strict(cap: Any) -> None:
    policy = resolve_budget_policy(BUDGET_B_VERSION, budget_authorization_fixture())
    with pytest.raises(SafetyError):
        safe_runtime_options(cap, budget_policy=policy)


def test_b_plan_requires_separate_hardware_approval_without_mutation() -> None:
    plan = budget_b_plan()
    before = copy.deepcopy(plan)
    validate_plan(plan)
    validate_preflight(_evidence(plan), plan, now=NOW)
    request = approval_request(plan, "2026-10-12T12:10:00+00:00")
    assert request["approved"] is False and request["signed_by"] is None
    assert request["hardware_enabled"] is False and request["submission_approved"] is False
    assert request["budget_authorization"] == plan["budget_authorization"]
    with pytest.raises(SafetyError, match="explicit approval"):
        validate_human_approval(plan, request, now=NOW)
    with pytest.raises(SafetyError, match="explicit approval"):
        validate_human_approval(plan, plan["budget_authorization"], now=NOW)
    validate_approval(plan, synthetic_approval(plan), _evidence(plan), now=NOW)
    assert plan == before


@pytest.mark.parametrize(
    "change",
    [
        "missing-proof",
        "bad-proof",
        "smoke-cap",
        "main-cap",
        "global-cap",
        "missing-map",
        "extra-map",
        "dd",
        "wrong-id",
        "budget-hash",
    ],
)
def test_changed_b_contract_rejects_before_reservation(experiment_b: Any, change: str) -> None:
    plan, paths, ledger, adapter = experiment_b
    approval = synthetic_approval(plan)
    if change == "missing-proof":
        plan.pop("budget_authorization")
    elif change == "bad-proof":
        plan["budget_authorization"]["hardware_submission_authorized"] = True
    elif change == "smoke-cap":
        plan["job_runtime_options"]["smoke"]["max_execution_time"] = 80
    elif change == "main-cap":
        plan["jobs"][1]["max_execution_time"] = 81
    elif change == "global-cap":
        plan["runtime_options"]["max_execution_time"] = 40
    elif change == "missing-map":
        del plan["job_runtime_options"]["smoke"]
    elif change == "extra-map":
        plan["job_runtime_options"]["unknown"] = copy.deepcopy(plan["runtime_options"])
    elif change == "dd":
        plan["job_runtime_options"]["smoke"]["dynamical_decoupling"]["enable"] = True
    elif change == "wrong-id":
        plan["jobs"][0]["id"] = "other-smoke"
    else:
        plan["budget"]["budget_authorization_sha256"] = "b" * 64
    with pytest.raises(SafetyError):
        ledger.submit_next(plan, approval, _evidence(plan), adapter, paths, now=NOW)
    assert not adapter.calls and not ledger.path.exists()


def test_b_smoke_cap_and_full_reservation_need_fresh_free_quota() -> None:
    plan = budget_b_plan()
    for usage, expected in ((179, "quota"), (180, None)):
        evidence = _evidence(plan)
        evidence["usage"]["remaining_seconds"] = usage
        if expected:
            with pytest.raises(SafetyError, match=expected):
                validate_preflight(evidence, plan, now=NOW)
        else:
            validate_preflight(evidence, plan, now=NOW)
    for key, value in (("plan", "paid"), ("pricing_type", "paid"), ("cost_eur", 1)):
        evidence = _evidence(plan)
        evidence[key] = value
        with pytest.raises(SafetyError):
            validate_preflight(evidence, plan, now=NOW)


def test_b_payload_seal_binds_full_proof_and_actual_job_options() -> None:
    plan = budget_b_plan()
    original = plan_payload_hash(plan)
    for key in ("budget_authorization", "job_runtime_options", "budget_policy_version"):
        changed = copy.deepcopy(plan)
        changed[key] = None
        assert plan_payload_hash(changed) != original
    validate_limit_assessment(plan, service_limit_policy())
    assert runtime_options_for_job(plan, "smoke")["max_execution_time"] == 20
    assert runtime_options_for_job(plan, "main-block-1")["max_execution_time"] == 80
    with pytest.raises(SafetyError):
        runtime_options_for_job(plan, "unapproved-job")


def test_public_budget_authorization_is_preserved_without_whitelisting_secrets() -> None:
    proof = budget_authorization_fixture()
    result = redact(
        {
            "budget_authorization": proof,
            "budget_authorization_sha256": proof["content_sha256"],
            "Authorization": "Bearer forbidden",
        }
    )
    assert result["budget_authorization"] == proof
    assert result["budget_authorization_sha256"] == proof["content_sha256"]
    assert result["Authorization"] == "[REDACTED]"
    for invalid in ({**proof, "token": "forbidden"}, {"token": "forbidden"}, None):
        assert redact({"budget_authorization": invalid})["budget_authorization"] == "[REDACTED]"
    assert (
        redact({"budget_authorization_sha256": "Bearer forbidden"})["budget_authorization_sha256"]
        == "[REDACTED]"
    )


def test_v1_approval_and_payload_schema_have_no_new_budget_fields() -> None:
    plan = synthetic_plan()
    request = approval_request(plan, "2026-10-12T12:10:00+00:00")
    assert (
        not {"budget_policy_version", "budget_authorization", "job_runtime_options"}
        & request.keys()
    )
    assert plan_budget_policy(plan).max_total_qpu_seconds == 120
    assert all(
        runtime_options_for_job(plan, job["id"])["max_execution_time"] == 40 for job in plan["jobs"]
    )


def test_b_ledger_persists_20_80_80_before_mock_submit_and_collect(experiment_b: Any) -> None:
    plan, _, ledger, adapter = experiment_b
    original = plan_hash(plan)
    for index, cap in enumerate((20, 80, 80)):
        record = _submit_b(experiment_b)
        assert record["reserved_qpu_seconds"] == cap
        assert record["requested_runtime_options"]["max_execution_time"] == cap
        assert (
            record["approval_evidence"]["budget_authorization_sha256"]
            == plan["budget_authorization"]["content_sha256"]
        )
        summary = ledger.collect(plan, adapter)
        assert summary["reserved_qpu_seconds"] == sum((20, 80, 80)[: index + 1])
    assert summary["status"] == "OFFLINE_READY" and summary["hardware_used"] is False
    assert sum(row["shots"] for row in summary["raw_results"]) == 49664
    assert len(summary["raw_results"]) == 52 and len(adapter.calls) == 3
    state = ledger.snapshot()
    assert state["budget_authorization"] == plan["budget_authorization"]
    assert state["budget_policy_version"] == BUDGET_B_VERSION
    assert plan_hash(plan) == original
    with pytest.raises(SafetyError, match="already been reserved"):
        _submit_b(experiment_b)


def test_b_ambiguous_smoke_keeps_20_seconds_and_never_resubmits(experiment_b: Any) -> None:
    plan, paths, ledger, adapter = experiment_b
    adapter.raise_after_accept = True
    record = _submit_b(experiment_b)
    assert record["status"] == "SUBMISSION_UNKNOWN" and record["reserved_qpu_seconds"] == 20
    assert "private-test-token" not in ledger.path.read_text(encoding="utf-8")
    resumed = JobLedger(ledger.path)
    assert resumed.collect(plan, adapter)["status"] == "SUBMISSION_UNKNOWN"
    with pytest.raises(SafetyError, match="Prior job"):
        resumed.submit_next(
            plan, synthetic_approval(plan), _evidence(plan), adapter, paths, now=NOW
        )
    assert len(adapter.calls) == 1 and resumed.snapshot()["reserved_qpu_seconds"] == 20


@pytest.mark.parametrize(
    "field",
    [
        "budget_authorization_sha256",
        "budget_policy_version",
        "requested_runtime_options",
        "approval_evidence",
    ],
)
def test_b_resume_rejects_changed_persistent_budget_binding(experiment_b: Any, field: str) -> None:
    plan, _, ledger, adapter = experiment_b
    _submit_b(experiment_b)
    state = ledger.snapshot()
    state["jobs"][0][field] = "changed"
    ledger.path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(SafetyError):
        ledger.collect(plan, adapter)
    assert len(adapter.calls) == 1


def test_b_hardware_adapter_must_bind_proof_before_reservation(experiment_b: Any) -> None:
    plan, paths, ledger, adapter = experiment_b
    adapter.hardware_used = True
    with pytest.raises(SafetyError, match="adapter lacks"):
        ledger.submit_next(plan, synthetic_approval(plan), _evidence(plan), adapter, paths, now=NOW)
    assert not adapter.calls and not ledger.path.exists()
    config = LocalProviderConfig(
        instance="crn:v1:synthetic", backend="synthetic", token="synthetic-token"
    )
    real_boundary = IBMRuntimeAdapter(config, budget_policy=plan_budget_policy(plan))
    assert (
        real_boundary.budget_policy.budget_authorization_sha256
        == plan["budget_authorization"]["content_sha256"]
    )
