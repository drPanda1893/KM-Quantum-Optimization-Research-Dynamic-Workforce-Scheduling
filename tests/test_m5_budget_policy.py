"""Explicit budget-only B authorization never changes default or hardware permission."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from km_quantum.m5.budget_policy import (
    BUDGET_B_SOURCE_REQUEST_SHA256,
    BUDGET_B_VERSION,
    BUDGET_V1_VERSION,
    approved_budget_b_payload,
    resolve_budget_policy,
    validate_budget_authorization,
)
from km_quantum.m5.io import canonical_hash
from km_quantum.m5.models import M5Config


def authorization_fixture() -> dict:
    """Synthetic representation of an explicit budget-only user decision."""
    payload = {
        "artifact_type": "m5_budget_authorization",
        "schema_version": 1,
        "budget_policy_version": BUDGET_B_VERSION,
        "scope": "budget_only",
        "approved": True,
        "authorized_by": "Florian",
        "authorized_at": "2026-10-13T10:00:00+00:00",
        "source_head": "5c186536d9b383d66d7a209f2835ccf3748e9b3e",
        "source_request_sha256": BUDGET_B_SOURCE_REQUEST_SHA256,
        "approved_budget": approved_budget_b_payload(),
        "hardware_enabled": False,
        "submission_approved": False,
        "hardware_submission_authorized": False,
    }
    return {**payload, "content_sha256": canonical_hash(payload)}


def test_v1_default_and_serialized_historical_config_remain_exact() -> None:
    config = M5Config()
    policy = config.resolved_budget_policy()
    assert policy.version == BUDGET_V1_VERSION
    assert policy.per_job_qpu_seconds == {"smoke": 40, "main-block-1": 40, "main-block-2": 40}
    assert policy.max_total_qpu_seconds == 120
    assert policy.budget_authorization_sha256 is None
    historical = config.model_dump(mode="json")
    assert "budget_policy_version" not in historical
    assert "budget_authorization" not in historical
    assert M5Config.model_validate(historical).model_dump(mode="json") == historical
    assert config.hardware_enabled is False and config.submission_approved is False


def test_b_requires_explicit_version_proof_and_both_scalar_guards() -> None:
    record = authorization_fixture()
    config = M5Config(
        budget_policy_version=BUDGET_B_VERSION,
        budget_authorization=record,
        max_qpu_seconds_per_job=80,
        max_total_qpu_seconds=180,
    )
    policy = config.resolved_budget_policy()
    assert policy.per_job_qpu_seconds == {"smoke": 20, "main-block-1": 80, "main-block-2": 80}
    assert policy.cap_for_job("smoke") == 20
    assert config.execution_time_cap("main-block-2") == 80
    assert policy.budget_authorization_sha256 == record["content_sha256"]
    assert config.model_dump(mode="json")["budget_policy_version"] == BUDGET_B_VERSION
    assert config.hardware_enabled is False and config.submission_approved is False
    for arguments in (
        {"max_qpu_seconds_per_job": 80, "max_total_qpu_seconds": 180},
        {
            "budget_policy_version": BUDGET_B_VERSION,
            "max_qpu_seconds_per_job": 80,
            "max_total_qpu_seconds": 180,
        },
        {"budget_policy_version": BUDGET_B_VERSION, "budget_authorization": record},
        {"budget_authorization": record},
    ):
        with pytest.raises((ValidationError, ValueError)):
            M5Config(**arguments)


@pytest.mark.parametrize(
    "change",
    [
        "head",
        "request",
        "scope",
        "budget",
        "jobs",
        "pubs",
        "shots",
        "cost",
        "paid",
        "flag",
        "timestamp",
        "seal",
        "extra",
    ],
)
def test_authorization_rejects_even_resealed_wrong_scope_or_limits(change: str) -> None:
    record = authorization_fixture()
    if change == "head":
        record["source_head"] = "b" * 40
    elif change == "request":
        record["source_request_sha256"] = "b" * 64
    elif change == "scope":
        record["scope"] = "hardware_submission"
    elif change == "budget":
        record["approved_budget"]["per_job_qpu_seconds"]["smoke"] = 21
    elif change == "jobs":
        record["approved_budget"]["max_provider_jobs"] = 4
    elif change == "pubs":
        record["approved_budget"]["max_pubs"] = 53
    elif change == "shots":
        record["approved_budget"]["max_total_shots"] = 49665
    elif change == "cost":
        record["approved_budget"]["max_cost_eur"] = 1
    elif change == "paid":
        record["approved_budget"]["required_pricing_type"] = "paid"
    elif change == "flag":
        record["hardware_submission_authorized"] = True
    elif change == "timestamp":
        record["authorized_at"] = "2026-10-13T10:00:00"
    elif change == "extra":
        record["token"] = "forbidden-field"
    else:
        record["content_sha256"] = "c" * 64
    if change != "seal":
        record["content_sha256"] = canonical_hash(
            {key: value for key, value in record.items() if key != "content_sha256"}
        )
    with pytest.raises((ValueError, ValidationError)):
        validate_budget_authorization(record)


def test_resolver_is_strict_and_returns_immutable_policy_with_independent_maps() -> None:
    record = authorization_fixture()
    before = copy.deepcopy(record)
    policy = resolve_budget_policy(BUDGET_B_VERSION, record)
    assert record == before
    view = policy.per_job_qpu_seconds
    view["smoke"] = 99
    assert policy.cap_for_job("smoke") == 20
    with pytest.raises(ValueError):
        policy.cap_for_job("unknown-job")
    for version, proof in (
        ("unknown", None),
        (BUDGET_B_VERSION, None),
        (BUDGET_V1_VERSION, record),
    ):
        with pytest.raises(ValueError):
            resolve_budget_policy(version, proof)


@pytest.mark.parametrize("change", ["float", "bool", "concurrent", "approved", "hwflag"])
def test_record_and_config_do_not_coerce_or_expand_permissions(change: str) -> None:
    record = authorization_fixture()
    if change == "float":
        record["approved_budget"]["per_job_qpu_seconds"]["main-block-1"] = 80.0
    elif change == "bool":
        record["approved_budget"]["max_provider_jobs"] = True
    elif change == "concurrent":
        record["approved_budget"]["max_concurrent_unresolved_own_jobs"] = 2
    elif change == "approved":
        record["approved"] = 1
    else:
        record["hardware_enabled"] = True
    record["content_sha256"] = canonical_hash(
        {key: value for key, value in record.items() if key != "content_sha256"}
    )
    with pytest.raises((ValueError, ValidationError)):
        validate_budget_authorization(record)


def test_nested_authorization_mutation_cannot_change_a_validated_config_policy() -> None:
    config = M5Config(
        budget_policy_version=BUDGET_B_VERSION,
        budget_authorization=authorization_fixture(),
        max_qpu_seconds_per_job=80,
        max_total_qpu_seconds=180,
    )
    assert config.budget_authorization is not None
    config.budget_authorization.approved_budget["per_job_qpu_seconds"]["smoke"] = 80
    with pytest.raises((ValueError, ValidationError)):
        config.execution_time_cap("smoke")
