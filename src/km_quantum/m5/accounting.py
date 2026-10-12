"""Strict measured usage for completed ledger prefixes, without provider access."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from km_quantum.m5.approvals import plan_hash, validate_plan
from km_quantum.m5.budget_policy import BUDGET_B_VERSION
from km_quantum.m5.provider import SafetyError, plan_budget_policy, runtime_options_for_job


def usage_metrics_missing(record: Mapping[str, Any]) -> bool:
    """Only absent/null usage may be refreshed; malformed observed values stay evidence."""
    metrics = record.get("provider_metrics")
    if metrics is None:
        return True
    if not isinstance(metrics, Mapping):
        return False
    usage = metrics.get("usage")
    if usage is None:
        return True
    return isinstance(usage, Mapping) and (
        "quantum_seconds" not in usage or usage["quantum_seconds"] is None
    )


def _actual_seconds(record: Mapping[str, Any]) -> float:
    identity = record.get("job_id")
    if usage_metrics_missing(record):
        raise SafetyError(
            f"Actual QPU usage is unavailable for completed job {identity}",
            diagnostic_kind="ACTUAL_USAGE_UNAVAILABLE",
        )
    metrics = record.get("provider_metrics")
    usage = metrics.get("usage") if isinstance(metrics, Mapping) else None
    value = usage.get("quantum_seconds") if isinstance(usage, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SafetyError(
            f"Actual QPU usage must be a finite nonnegative number for job {identity}",
            diagnostic_kind="ACTUAL_USAGE_INVALID",
        )
    try:
        seconds = float(value)
    except (ValueError, OverflowError):
        seconds = math.nan
    if not math.isfinite(seconds) or seconds < 0:
        raise SafetyError(
            f"Actual QPU usage must be finite and nonnegative for job {identity}",
            diagnostic_kind="ACTUAL_USAGE_INVALID",
        )
    return seconds


def validate_completed_job_usage(
    plan: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Charge every collected prior job before any new reservation or run.

    Missing measurements never stand for zero. Pending, failed, unknown or
    contract-invalid records cannot be treated as completed predecessors.
    An empty, valid plan prefix has exactly zero observed jobs and usage.
    This function never reads a job, account, ledger file or provider service.
    """
    validate_plan(plan)
    policy = plan_budget_policy(plan)
    digest = plan_hash(plan)
    if (
        not isinstance(records, (list, tuple))
        or len(records) > len(plan["jobs"])
        or any(not isinstance(row, Mapping) for row in records)
    ):
        raise SafetyError(
            "Completed ledger prefix is malformed", diagnostic_kind="LEDGER_ACCOUNTING_ERROR"
        )
    expected_jobs = plan["jobs"][: len(records)]
    ids = [record.get("job_id") for record in records]
    if (
        any(not isinstance(identity, str) or not identity for identity in ids)
        or ids != [job["id"] for job in expected_jobs]
        or len(set(ids)) != len(ids)
    ):
        raise SafetyError(
            "Completed ledger job order differs from the plan",
            diagnostic_kind="LEDGER_ACCOUNTING_ERROR",
        )
    provider_ids: list[str] = []
    entries, seconds_per_job, over_caps = [], [], []
    for record, job in zip(records, expected_jobs, strict=True):
        identity = job["id"]
        provider_id = record.get("provider_job_id")
        if (
            record.get("status") != "COLLECTED"
            or record.get("contract_verified") is not True
            or not isinstance(record.get("provider_status"), str)
            or record["provider_status"] not in {"DONE", "COMPLETED"}
        ):
            raise SafetyError(
                f"Prior job {identity} is pending, failed, unknown or unverified",
                diagnostic_kind="PRIOR_JOB_UNRESOLVED",
            )
        if (
            not isinstance(provider_id, str)
            or not provider_id.strip()
            or provider_id in provider_ids
        ):
            raise SafetyError(
                "Collected provider job identity is invalid or duplicated",
                diagnostic_kind="LEDGER_ACCOUNTING_ERROR",
            )
        provider_ids.append(provider_id)
        cap = policy.cap_for_job(identity)
        if (
            record.get("plan_hash") != digest
            or record.get("pubs") != job["pubs"]
            or type(record.get("reserved_qpu_seconds")) is not int
            or record["reserved_qpu_seconds"] != cap
            or record.get("requested_runtime_options") != runtime_options_for_job(plan, identity)
        ):
            raise SafetyError(
                "Collected job inputs or cap differ from the approved plan",
                diagnostic_kind="LEDGER_ACCOUNTING_ERROR",
            )
        if policy.version == BUDGET_B_VERSION:
            approval = record.get("approval_evidence")
            if (
                record.get("budget_policy_version") != policy.version
                or record.get("budget_authorization_sha256") != policy.budget_authorization_sha256
                or not isinstance(approval, Mapping)
                or approval.get("budget_policy_version") != policy.version
                or approval.get("budget_authorization_sha256") != policy.budget_authorization_sha256
            ):
                raise SafetyError(
                    "Collected job budget proof differs from the approved plan",
                    diagnostic_kind="LEDGER_ACCOUNTING_ERROR",
                )
        if (
            not isinstance(record.get("source_kind"), str)
            or record["source_kind"] not in {"mock", "ibm_hardware"}
            or record.get("hardware_used") is not (record.get("source_kind") == "ibm_hardware")
        ):
            raise SafetyError(
                "Collected usage provenance is incomplete or inconsistent",
                diagnostic_kind="LEDGER_ACCOUNTING_ERROR",
            )
        seconds = _actual_seconds(record)
        seconds_per_job.append(seconds)
        if seconds > cap:
            over_caps.append(identity)
        entries.append(
            {
                "job_id": identity,
                "provider_job_id": provider_id,
                "actual_qpu_seconds": seconds,
                "reviewed_job_cap_seconds": cap,
                "source_kind": record["source_kind"],
                "hardware_used": record["hardware_used"],
            }
        )
    try:
        total = math.fsum(seconds_per_job)
    except OverflowError:
        raise SafetyError(
            "Actual QPU usage exceeds reviewed per-job and campaign bounds (sum overflow)",
            diagnostic_kind="ACTUAL_USAGE_LIMIT_EXCEEDED",
        ) from None
    if over_caps or total > policy.max_total_qpu_seconds:
        parts = ["per-job cap: " + ", ".join(over_caps)] if over_caps else []
        if total > policy.max_total_qpu_seconds:
            parts.append("campaign cap")
        raise SafetyError(
            "Actual QPU usage exceeds reviewed " + "; ".join(parts),
            diagnostic_kind="ACTUAL_USAGE_LIMIT_EXCEEDED",
        )
    return {
        "schema_version": 1,
        "artifact_type": "m5_actual_usage_accounting",
        "status": "PASS",
        "plan_hash": digest,
        "budget_policy_version": policy.version,
        "budget_authorization_sha256": policy.budget_authorization_sha256,
        "completed_job_count": len(records),
        "actual_qpu_seconds_per_job": seconds_per_job,
        "actual_total_qpu_seconds": total,
        "max_total_qpu_seconds": policy.max_total_qpu_seconds,
        "remaining_campaign_qpu_seconds": policy.max_total_qpu_seconds - total,
        "job_usage": entries,
        "actual_provider_metrics_available": bool(records),
        "hardware_measurements_available": bool(records)
        and all(row.get("hardware_used") is True for row in records),
        "read_only": True,
        "provider_contacted": False,
    }
