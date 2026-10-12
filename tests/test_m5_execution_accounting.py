"""Fail-closed measured usage before reservations; read-only metrics recovery."""

from __future__ import annotations

import copy
import json
import math
from typing import Any
from unittest.mock import Mock

import pytest
from test_m5_approval import NOW, synthetic_approval, synthetic_plan, synthetic_preflight
from test_m5_budget_b_provider import budget_b_plan
from test_m5_ledger import experiment as experiment

from km_quantum.m5.accounting import validate_completed_job_usage
from km_quantum.m5.approvals import plan_hash
from km_quantum.m5.budget_policy import BUDGET_B_VERSION
from km_quantum.m5.io import canonical_hash
from km_quantum.m5.provider import SafetyError, plan_budget_policy, runtime_options_for_job


def collected_records(plan: dict, values: list[Any]) -> list[dict]:
    policy = plan_budget_policy(plan)
    records = []
    for index, (job, seconds) in enumerate(zip(plan["jobs"], values, strict=False)):
        record = {
            "job_id": job["id"],
            "provider_job_id": f"synthetic-{index}",
            "plan_hash": plan_hash(plan),
            "pubs": copy.deepcopy(job["pubs"]),
            "requested_runtime_options": runtime_options_for_job(plan, job["id"]),
            "reserved_qpu_seconds": job["max_execution_time"],
            "status": "COLLECTED",
            "provider_status": "DONE",
            "contract_verified": True,
            "source_kind": "mock",
            "hardware_used": False,
            "provider_metrics": {"usage": {"quantum_seconds": seconds}},
        }
        if policy.version == BUDGET_B_VERSION:
            record.update(
                budget_policy_version=policy.version,
                budget_authorization_sha256=policy.budget_authorization_sha256,
                approval_evidence={
                    "budget_policy_version": policy.version,
                    "budget_authorization_sha256": policy.budget_authorization_sha256,
                },
            )
        records.append(record)
    return records


def test_empty_and_completed_usage_are_independently_accounted_with_exact_budget() -> None:
    plan = budget_b_plan()
    empty = validate_completed_job_usage(plan, [])
    assert empty["actual_total_qpu_seconds"] == 0
    assert empty["completed_job_count"] == 0
    records = collected_records(plan, [20, 80, 80])
    before = copy.deepcopy(records)
    summary = validate_completed_job_usage(plan, records)
    assert records == before
    assert summary["status"] == "PASS"
    assert summary["actual_qpu_seconds_per_job"] == [20, 80, 80]
    assert summary["actual_total_qpu_seconds"] == 180
    assert summary["remaining_campaign_qpu_seconds"] == 0
    assert summary["budget_authorization_sha256"] == plan["budget"]["budget_authorization_sha256"]


@pytest.mark.parametrize(
    "value", [None, True, False, -1, math.nan, math.inf, -math.inf, "2.5", {}, []]
)
def test_missing_or_invalid_actual_usage_never_becomes_zero_or_passes(value: Any) -> None:
    plan = budget_b_plan()
    records = collected_records(plan, [value])
    with pytest.raises(SafetyError) as caught:
        validate_completed_job_usage(plan, records)
    assert caught.value.diagnostic_kind in {"ACTUAL_USAGE_UNAVAILABLE", "ACTUAL_USAGE_INVALID"}


@pytest.mark.parametrize(
    "change",
    [
        "status",
        "contract",
        "provider_status",
        "job",
        "hash",
        "pub",
        "options",
        "proof",
        "provider_id",
    ],
)
def test_accounting_requires_exact_completed_record_order_and_bound_contract(change: str) -> None:
    plan = budget_b_plan()
    records = collected_records(plan, [2.5])
    if change == "status":
        records[0]["status"] = "SUBMISSION_UNKNOWN"
    elif change == "contract":
        records[0]["contract_verified"] = False
    elif change == "provider_status":
        records[0]["provider_status"] = "RUNNING"
    elif change == "job":
        records[0]["job_id"] = "main-block-1"
    elif change == "hash":
        records[0]["plan_hash"] = "a" * 64
    elif change == "pub":
        records[0]["pubs"][0]["shots"] = 1024
    elif change == "options":
        records[0]["requested_runtime_options"]["max_execution_time"] = 80
    elif change == "proof":
        records[0]["budget_authorization_sha256"] = "a" * 64
    else:
        records[0]["provider_job_id"] = None
    with pytest.raises(SafetyError):
        validate_completed_job_usage(plan, records)


@pytest.mark.parametrize("values", [[20.01], [10, 80.01], [20, 80, 80.01]])
def test_actual_b_job_and_campaign_excess_block(values: list[float]) -> None:
    plan = budget_b_plan()
    with pytest.raises(SafetyError) as caught:
        validate_completed_job_usage(plan, collected_records(plan, values))
    assert caught.value.diagnostic_kind == "ACTUAL_USAGE_LIMIT_EXCEEDED"
    if len(values) == 3:
        assert "campaign" in str(caught.value).lower()


def test_actual_v1_usage_retains_40_second_job_cap() -> None:
    plan = synthetic_plan()
    with pytest.raises(SafetyError, match="per-job"):
        validate_completed_job_usage(plan, collected_records(plan, [40.01]))


def submit_mock(experiment: Any) -> dict:
    plan, paths, ledger, adapter = experiment
    return ledger.submit_next(
        plan, synthetic_approval(plan), synthetic_preflight(plan), adapter, paths, now=NOW
    )


@pytest.mark.parametrize("value", [None, True, -1, math.nan, 40.01])
def test_next_mock_job_blocks_before_qpy_load_reservation_or_run(
    experiment: Any, value: Any
) -> None:
    plan, _, ledger, adapter = experiment
    submit_mock(experiment)
    adapter.jobs["synthetic-job-1"].metrics = Mock(
        return_value={"usage": {"quantum_seconds": value}}
    )
    ledger.collect(plan, adapter)
    before = ledger.snapshot()
    with pytest.raises(SafetyError):
        submit_mock(experiment)
    assert ledger.snapshot() == before
    assert ledger.snapshot()["reserved_qpu_seconds"] == 40
    assert len(adapter.calls) == 1


def test_collected_missing_metrics_refreshes_once_without_refetching_result_or_status(
    experiment: Any,
) -> None:
    plan, _, ledger, adapter = experiment
    submit_mock(experiment)
    job = adapter.jobs["synthetic-job-1"]
    job.metrics = Mock(side_effect=[{}, {"usage": {"quantum_seconds": 2.5}}])
    first = ledger.collect(plan, adapter)
    assert first["actual_usage_accounting_status"] == "BLOCKED"
    saved_counts = copy.deepcopy(first["raw_results"])
    result_calls, status_calls = job.result_calls, job.status_calls
    second = ledger.collect(plan, adapter)
    assert second["actual_usage_accounting_status"] == "PASS"
    assert second["raw_results"] == saved_counts
    assert job.metrics.call_count == 2
    assert job.result_calls == result_calls and job.status_calls == status_calls
    assert second["job_records"][0]["provider_metrics_history"] == [{}]
    submit_mock(experiment)
    assert len(adapter.calls) == 2


def test_metrics_exception_preserves_collected_counts_and_allows_metrics_only_recovery(
    experiment: Any,
) -> None:
    plan, _, ledger, adapter = experiment
    submit_mock(experiment)
    job = adapter.jobs["synthetic-job-1"]
    job.metrics = Mock(
        side_effect=[RuntimeError("metrics not ready"), {"usage": {"quantum_seconds": 2.5}}]
    )
    first = ledger.collect(plan, adapter)
    assert first["job_records"][0]["status"] == "COLLECTED"
    assert len(first["raw_results"]) == 4
    before = job.result_calls
    assert ledger.collect(plan, adapter)["actual_usage_accounting_status"] == "PASS"
    assert job.result_calls == before


def test_each_manual_metrics_refresh_is_bounded_without_a_permanent_retry_lock(
    experiment: Any,
) -> None:
    plan, _, ledger, adapter = experiment
    submit_mock(experiment)
    job = adapter.jobs["synthetic-job-1"]
    job.metrics = Mock(return_value={})
    ledger.collect(plan, adapter)
    for _ in range(6):
        summary = ledger.collect(plan, adapter)
    assert job.metrics.call_count == 7  # one initial read and six explicit manual collects
    assert job.result_calls == 1
    assert job.status_calls == 1
    assert summary["actual_usage_accounting_status"] == "BLOCKED"
    assert summary["job_records"][0]["metrics_refresh_attempts"] == 6
    assert ledger.snapshot()["reserved_qpu_seconds"] == 40


def test_invalid_observed_metrics_are_retained_and_not_refreshed_to_hide_violation(
    experiment: Any,
) -> None:
    plan, _, ledger, adapter = experiment
    submit_mock(experiment)
    job = adapter.jobs["synthetic-job-1"]
    job.metrics = Mock(
        return_value={"usage": {"quantum_seconds": -0.5}, "note": "raw original value"}
    )
    first = ledger.collect(plan, adapter)
    assert first["job_records"][0]["provider_metrics"]["usage"]["quantum_seconds"] == -0.5
    second = ledger.collect(plan, adapter)
    assert job.metrics.call_count == 1
    assert first["raw_results"] == second["raw_results"]
    assert second["actual_usage_accounting_status"] == "BLOCKED"


def test_valid_collected_records_are_skipped_without_any_ledger_mutation(experiment: Any) -> None:
    plan, _, ledger, adapter = experiment
    submit_mock(experiment)
    job = adapter.jobs["synthetic-job-1"]
    job.metrics = Mock(return_value={"usage": {"quantum_seconds": 2.5}})
    ledger.collect(plan, adapter)
    before = ledger.path.read_bytes()
    ledger.collect(plan, adapter)
    assert ledger.path.read_bytes() == before
    assert job.result_calls == job.status_calls == job.metrics.call_count == 1


def test_nonfinite_observation_is_archived_explicitly_without_numeric_fallback(
    experiment: Any,
) -> None:
    plan, _, ledger, adapter = experiment
    submit_mock(experiment)
    job = adapter.jobs["synthetic-job-1"]
    job.metrics = Mock(return_value={"usage": {"quantum_seconds": math.nan}})
    summary = ledger.collect(plan, adapter)
    record = summary["job_records"][0]
    assert record["provider_metrics"]["usage"]["quantum_seconds"] == {
        "__m5_nonfinite_float__": "nan"
    }
    assert record["provider_metrics_observations"][0]["payload"] == record["provider_metrics"]
    assert record["status"] == "COLLECTED" and len(summary["raw_results"]) == 4
    assert summary["actual_usage_accounting_diagnostic_kind"] == "ACTUAL_USAGE_INVALID"
    assert ledger.snapshot()["reserved_qpu_seconds"] == 40


@pytest.mark.parametrize("field", ["job_id", "provider_status", "source_kind"])
def test_unexpected_metadata_containers_fail_closed_with_typed_diagnosis(field: str) -> None:
    plan = budget_b_plan()
    records = collected_records(plan, [2.5])
    records[0][field] = []
    with pytest.raises(SafetyError):
        validate_completed_job_usage(plan, records)


def test_finite_actual_usage_sum_overflow_is_a_typed_budget_failure() -> None:
    plan = budget_b_plan()
    with pytest.raises(SafetyError) as caught:
        validate_completed_job_usage(plan, collected_records(plan, [1e308, 1e308, 1e308]))
    assert caught.value.diagnostic_kind == "ACTUAL_USAGE_LIMIT_EXCEEDED"
    assert "campaign" in str(caught.value)


def live_assessment(plan: dict) -> dict:
    body = {
        "artifact_type": "m5_live_device_binding_assessment",
        "status": "PASS",
        "plan_hash": plan_hash(plan),
        "backend": plan["backend"],
        "instance_binding": plan["instance_binding"],
        "frozen_snapshot_sha256": plan["target_snapshot_sha256"],
        "current_snapshot_sha256": "c" * 64,
        "validated_at": NOW,
        "parameters_unchanged": True,
    }
    return {**body, "content_sha256": canonical_hash(body)}


def test_optional_live_assessment_is_sealed_and_bound_to_reserved_record(experiment: Any) -> None:
    plan, paths, ledger, adapter = experiment
    evidence = synthetic_preflight(plan)
    evidence["live_device_binding"] = live_assessment(plan)
    record = ledger.submit_next(plan, synthetic_approval(plan), evidence, adapter, paths, now=NOW)
    assert (
        record["approval_evidence"]["live_device_binding_sha256"]
        == evidence["live_device_binding"]["content_sha256"]
    )


def test_tampered_live_assessment_blocks_before_reservation_and_mock_run(experiment: Any) -> None:
    plan, paths, ledger, adapter = experiment
    evidence = synthetic_preflight(plan)
    evidence["live_device_binding"] = live_assessment(plan)
    evidence["live_device_binding"]["current_snapshot_sha256"] = "d" * 64
    with pytest.raises(SafetyError) as caught:
        ledger.submit_next(plan, synthetic_approval(plan), evidence, adapter, paths, now=NOW)
    assert caught.value.diagnostic_kind == "LIVE_DEVICE_BINDING_ERROR"
    assert ledger.snapshot()["reserved_qpu_seconds"] == 0 and not adapter.calls


def test_mock_smoke_cannot_become_a_predecessor_for_a_real_adapter(experiment: Any) -> None:
    plan, _, ledger, adapter = experiment
    submit_mock(experiment)
    ledger.collect(plan, adapter)
    before = ledger.snapshot()
    adapter.hardware_used = True  # synthetic impersonation only; no Runtime backend exists
    with pytest.raises(SafetyError) as caught:
        submit_mock(experiment)
    assert caught.value.diagnostic_kind == "HARDWARE_PREDECESSOR_REQUIRED"
    assert ledger.snapshot() == before and len(adapter.calls) == 1


def test_lazy_named_synthetic_secret_is_removed_from_all_collection_metadata(
    experiment: Any,
) -> None:
    plan, _, ledger, adapter = experiment
    submit_mock(experiment)
    job = adapter.jobs["synthetic-job-1"]
    synthetic_secret = "SYNTHETIC-NAMED-CREDENTIAL-X7Y9-not-real"
    connected = {"value": False}
    original_lookup = adapter.job

    def lazy_lookup(identity: str) -> Any:
        connected["value"] = True
        return original_lookup(identity)

    adapter.job = lazy_lookup
    adapter._secret_values = lambda: (synthetic_secret,) if connected["value"] else ()
    original_result = job.result

    def with_private_metadata(*, timeout: float) -> Any:
        result = original_result(timeout=timeout)
        result.metadata["opaque_note"] = synthetic_secret
        for pub in result:
            pub.metadata["opaque_note"] = synthetic_secret
        return result

    job.result = with_private_metadata
    job.metrics = Mock(
        return_value={"usage": {"quantum_seconds": 2.5}, "opaque_note": synthetic_secret}
    )
    summary = ledger.collect(plan, adapter)
    assert synthetic_secret not in json.dumps(summary)
    assert synthetic_secret not in ledger.path.read_text(encoding="utf-8")
    assert summary["raw_results"][0]["provider_metadata"]["opaque_note"] == "[REDACTED]"
    assert summary["job_records"][0]["provider_metrics"]["opaque_note"] == "[REDACTED]"


def test_lazy_named_synthetic_secret_is_rebound_on_job_lookup_exception(experiment: Any) -> None:
    plan, _, ledger, adapter = experiment
    submit_mock(experiment)
    synthetic_secret = "SYNTHETIC-NAMED-CREDENTIAL-error-not-real"
    connected = {"value": False}

    def bad_lookup(identity: str) -> Any:
        connected["value"] = True
        raise RuntimeError("opaque failure " + synthetic_secret)

    adapter.job = bad_lookup
    adapter._secret_values = lambda: (synthetic_secret,) if connected["value"] else ()
    summary = ledger.collect(plan, adapter)
    assert synthetic_secret not in json.dumps(summary)
    assert synthetic_secret not in ledger.path.read_text(encoding="utf-8")
    assert summary["job_records"][0]["status"] == "PARTIAL"
