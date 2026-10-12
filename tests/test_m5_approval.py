"""Human sign-off binds the exact reviewed experiment and fresh free quota."""

import copy
from typing import Any

import pytest
from m5_contract_helpers import attach_execution_contracts, modern_evidence

from km_quantum.m5.approvals import (
    approval_request,
    plan_hash,
    validate_approval,
    validate_human_approval,
    validate_plan,
)
from km_quantum.m5.provider import SafetyError, safe_runtime_options

NOW = "2026-10-12T12:00:00+00:00"


def synthetic_plan(circuit_hash: str = "a" * 64) -> dict[str, Any]:
    """Explicit synthetic fixture, never an approval created by production code."""
    circuits = {f"circuit-{index}": {"qpy_sha256": circuit_hash} for index in range(28)}
    jobs = []
    for block, count in enumerate((4, 24, 24)):
        jobs.append(
            {
                "id": f"job-{block}",
                "kind": "smoke" if block == 0 else "main",
                "max_execution_time": 40,
                "pubs": [
                    {
                        "pub_id": f"block-{block}-pub-{index}",
                        "circuit_id": f"circuit-{index if block == 0 else index + 4}",
                        "circuit_hash": circuit_hash,
                        "shots": 128 if block == 0 else 1024,
                        "num_bits": 3,
                    }
                    for index in range(count)
                ],
            }
        )
    return attach_execution_contracts(
        {
            "schema_version": 1,
            "artifact_type": "m5_hardware_execution_plan",
            "provider_verified": True,
            "source_kind": "ibm_hardware",
            "hardware_enabled": False,
            "submission_approved": False,
            "backend": "synthetic-contract-only",
            "instance_binding": "b" * 64,
            "layout": [9, 3, 6],
            "circuits": circuits,
            "jobs": jobs,
            "budget": {
                "max_provider_jobs": 3,
                "max_total_shots": 49664,
                "max_qpu_seconds_per_job": 40,
                "max_total_qpu_seconds": 120,
                "max_cost_eur": 0,
            },
            "runtime_options": safe_runtime_options(),
        }
    )


def synthetic_preflight(plan: dict[str, Any] | None = None) -> dict[str, Any]:
    return modern_evidence(
        {
            "status": "PROVIDER_PREFLIGHT_READY",
            "provider_verified": True,
            "source_kind": "ibm_hardware",
            "backend": "synthetic-contract-only",
            "instance_binding": "b" * 64,
            "plan": "open",
            "pricing_type": "free",
            "cost_eur": 0,
            "operational": True,
            "simulator": False,
            "fake": False,
            "runtime_version": "0.47.0",
            "single_job_contract": True,
            "obtained_at": NOW,
            "usage": {
                "remaining_seconds": 120,
                "consumed_seconds": 0,
                "limit_seconds": 120,
                "observed_at": NOW,
            },
            "limits": {"max_shots": 1024, "max_jobs": 3, "max_pubs": 24},
            "execution_estimates": {
                f"job-{index}": {"seconds": 20.0, "source": "synthetic test estimate"}
                for index in range(3)
            },
        },
        plan if plan is not None else synthetic_plan(),
    )


def synthetic_approval(plan: dict[str, Any]) -> dict[str, Any]:
    approval = approval_request(plan, "2026-10-12T12:10:00+00:00")
    approval.update(approved=True, signed_by="Florian", signed_at=NOW)
    return approval


def test_request_cannot_self_approve_and_does_not_mutate_plan() -> None:
    plan = synthetic_plan()
    before = copy.deepcopy(plan)
    request = approval_request(plan, "2026-10-12T12:10:00+00:00")
    assert request["approved"] is False
    assert request["signed_by"] is None
    assert request["hardware_enabled"] is False
    assert request["submission_approved"] is False
    assert plan == before
    with pytest.raises(SafetyError, match="explicit approval"):
        validate_approval(plan, request, synthetic_preflight(), now=NOW)


def test_synthetic_exact_approval_validates_without_mutating_frozen_flags() -> None:
    plan = synthetic_plan()
    digest = plan_hash(plan)
    validate_approval(plan, synthetic_approval(plan), synthetic_preflight(), now=NOW)
    assert plan_hash(plan) == digest
    assert plan["hardware_enabled"] is False


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("backend", "other-backend"),
        ("instance_binding", "c" * 64),
        ("layout", [6, 3, 9]),
        ("provider_verified", False),
        ("source_kind", "fake"),
        ("hardware_enabled", True),
    ],
)
def test_changed_plan_invalidates_existing_approval(key: str, value: Any) -> None:
    plan = synthetic_plan()
    approved = synthetic_approval(plan)
    plan[key] = value
    with pytest.raises(SafetyError):
        validate_approval(plan, approved, synthetic_preflight(), now=NOW)


@pytest.mark.parametrize("mutation", ["shots", "options", "hash", "budget", "pub_id"])
def test_scientific_or_cost_changes_need_new_approval(mutation: str) -> None:
    plan = synthetic_plan()
    approved = synthetic_approval(plan)
    if mutation == "shots":
        plan["jobs"][1]["pubs"][0]["shots"] = 2048
    elif mutation == "options":
        plan["runtime_options"]["twirling"]["enable_measure"] = True
    elif mutation == "hash":
        plan["circuits"]["circuit-0"]["qpy_sha256"] = "c" * 64
    elif mutation == "budget":
        plan["budget"]["max_total_qpu_seconds"] = 121
    else:
        plan["jobs"][0]["pubs"][0]["pub_id"] = "changed-id"
    with pytest.raises(SafetyError):
        validate_approval(plan, approved, synthetic_preflight(), now=NOW)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("plan", "pay-as-you-go"),
        ("pricing_type", "paid"),
        ("cost_eur", 1),
        ("simulator", True),
        ("fake", True),
        ("operational", False),
        ("source_kind", "mock"),
        ("single_job_contract", False),
        ("runtime_version", "0.50.0"),
    ],
)
def test_paid_fake_offline_or_expanding_provider_is_rejected(field: str, value: Any) -> None:
    plan = synthetic_plan()
    evidence = synthetic_preflight()
    evidence[field] = value
    with pytest.raises(SafetyError):
        validate_approval(plan, synthetic_approval(plan), evidence, now=NOW)


@pytest.mark.parametrize("missing", ["quota", "usage", "jobs", "estimate", "estimate_cap", "stale"])
def test_unknown_quota_limits_estimates_and_stale_facts_block(missing: str) -> None:
    plan = synthetic_plan()
    evidence = synthetic_preflight()
    if missing == "quota":
        evidence["usage"]["remaining_seconds"] = None
    elif missing == "usage":
        evidence["usage"]["consumed_seconds"] = None
    elif missing == "jobs":
        evidence["pending_jobs"]["complete"] = False
    elif missing == "estimate":
        evidence["execution_estimates"].pop("job-1")
    elif missing == "estimate_cap":
        evidence["execution_estimates"]["job-1"]["seconds"] = 41
    else:
        evidence["obtained_at"] = "2026-10-12T11:54:59+00:00"
    with pytest.raises(SafetyError):
        validate_approval(plan, synthetic_approval(plan), evidence, now=NOW)


@pytest.mark.parametrize("change", ["expired", "signer", "unsigned", "future", "copied_binding"])
def test_expiry_and_human_identity_are_required(change: str) -> None:
    plan = synthetic_plan()
    approval = synthetic_approval(plan)
    if change == "expired":
        approval["expires_at"] = NOW
    elif change == "signer":
        approval["signed_by"] = "agent"
    elif change == "unsigned":
        approval["signed_at"] = None
    elif change == "future":
        approval["signed_at"] = "2026-10-12T12:01:00+00:00"
    else:
        approval["backend"] = "other-backend"
    with pytest.raises(SafetyError):
        validate_approval(plan, approval, synthetic_preflight(), now=NOW)


def test_insufficient_reservation_and_missing_required_smoke_block() -> None:
    plan = synthetic_plan()
    evidence = synthetic_preflight()
    evidence["usage"]["remaining_seconds"] = 119.999
    with pytest.raises(SafetyError, match="full approved reservation"):
        validate_approval(plan, synthetic_approval(plan), evidence, now=NOW)
    plan["jobs"] = plan["jobs"][1:]
    with pytest.raises(SafetyError, match="smoke"):
        validate_plan(plan)


def test_consistent_hash_is_checked_if_stored_in_execution_plan() -> None:
    plan = synthetic_plan()
    plan["plan_hash"] = plan_hash(plan)
    validate_plan(plan)
    plan["layout"] = [3, 6, 9]
    with pytest.raises(SafetyError, match="hash"):
        validate_plan(plan)


@pytest.mark.parametrize(
    "mutation", ["expiry", "mirror", "unsigned", "ttl", "malformed", "timezone", "schema"]
)
def test_all_human_contract_errors_are_rejected_without_provider_validation(
    monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    import km_quantum.m5.approvals as approvals

    plan = synthetic_plan()
    approval = synthetic_approval(plan)
    if mutation == "expiry":
        approval["expires_at"] = NOW
    elif mutation == "mirror":
        approval["runtime_options"]["execution"]["init_qubits"] = False
    elif mutation == "unsigned":
        approval["signed_by"] = None
    elif mutation == "ttl":
        approval["expires_at"] = "2026-10-13T12:00:01+00:00"
    elif mutation == "malformed":
        approval["expires_at"] = "invalid date"
    elif mutation == "timezone":
        approval["signed_at"] = "2026-10-12T12:00:00"
    else:
        approval["schema_version"] = 99

    def never_provider(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(
            "An invalid human contract must be rejected before provider validation"
        )

    monkeypatch.setattr(approvals, "validate_preflight", never_provider)
    with pytest.raises(SafetyError):
        validate_approval(plan, approval, synthetic_preflight(), now=NOW)


def test_offline_human_validation_allows_exact_24_hour_limit() -> None:
    plan = synthetic_plan()
    approval = synthetic_approval(plan)
    approval["expires_at"] = "2026-10-13T12:00:00+00:00"
    validate_human_approval(plan, approval, now=NOW)
