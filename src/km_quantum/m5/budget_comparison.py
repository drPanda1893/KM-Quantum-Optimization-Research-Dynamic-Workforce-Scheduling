"""Exactly two advisory budget comparisons, isolated from submission schemas.

The source circuits, timing snapshot, Runtime options and active guards are
immutable inputs. This module creates no executable plan or approval request.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any, TypeGuard

from km_quantum.m5.approvals import plan_hash, validate_plan
from km_quantum.m5.io import canonical_hash
from km_quantum.m5.models import M5Config
from km_quantum.m5.usage import UsagePolicy, validate_usage_model

_FULL_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_JOB_IDS = ("smoke", "main-block-1", "main-block-2")
_PROPOSED_CAPS = {"smoke": 20, "main-block-1": 80, "main-block-2": 80}


def _nonnegative(value: Any) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _active_limits() -> dict[str, Any]:
    config = M5Config()
    return {
        "max_qpu_seconds_per_job": config.max_qpu_seconds_per_job,
        "max_total_qpu_seconds": config.max_total_qpu_seconds,
        "max_campaign_submissions": config.max_provider_jobs,
        "max_concurrent_unresolved_own_jobs": 1,
        "max_total_shots": config.max_total_shots,
        "max_cost_eur": config.max_cost_eur,
    }


def evaluate_budget(
    job_seconds: Mapping[str, float],
    job_caps: Mapping[str, float],
    *,
    total_cap_seconds: float,
) -> dict[str, Any]:
    """Compare local numbers only; this does not grant any execution authority."""
    if not job_seconds or set(job_seconds) != set(job_caps):
        raise ValueError("Budget comparison requires matching nonempty job identities")
    if (
        not _nonnegative(total_cap_seconds)
        or total_cap_seconds == 0
        or any(not _nonnegative(value) for value in job_seconds.values())
        or any(not _nonnegative(value) or value == 0 for value in job_caps.values())
    ):
        raise ValueError("Finite nonnegative estimates and positive limits are required")
    total = math.fsum(job_seconds.values())
    details = {
        identity: {
            "local_risk_seconds": seconds,
            "cap_seconds": job_caps[identity],
            "headroom_seconds": job_caps[identity] - seconds,
            "within_cap": seconds <= job_caps[identity],
        }
        for identity, seconds in job_seconds.items()
    }
    fits = total <= total_cap_seconds and all(row["within_cap"] for row in details.values())
    return {
        "units": "seconds",
        "fits_all_limits": fits,
        "within_total_cap": total <= total_cap_seconds,
        "total_seconds": total,
        "total_cap_seconds": total_cap_seconds,
        "total_headroom_seconds": total_cap_seconds - total,
        "job_details": details,
        "diagnostic_kind": None if fits else "LOCAL_CAMPAIGN_LIMIT_EXCEEDED",
        "is_provider_acceptance": False,
        "is_owner_approval": False,
    }


def summarize_job_time(
    job_model: Mapping[str, Any], *, shots_override: int | None = None
) -> dict[str, Any]:
    """Recalculate per-PUB duration contributions, keeping fixed policy assumptions.

    Native circuit duration already includes readout. Independent critical-path
    probing can further separate gates/readout; this sum never adds readout again.
    A zero-shot limit is an analytical scalar, never a valid PUB specification.
    """
    if shots_override is not None and (type(shots_override) is not int or shots_override <= 0):
        raise ValueError("A positive integral shot override is required")
    default_policy = UsagePolicy()
    policy = job_model.get("local_policy", {})
    if (
        policy.get("subjob_policy") != default_policy.subjob_policy
        or policy.get("safety_factor") != default_policy.safety_factor
        or policy.get("overhead_seconds_per_subjob") != 2.0
        or policy.get("max_subjobs") is not None
        or policy.get("reviewed_source") is not None
        or policy.get("is_provider_fact") is not False
    ):
        raise ValueError("Comparison must retain the source's conservative default risk policy")
    if job_model.get("central_subjobs") != 1:
        raise ValueError("Comparison requires the recorded one-subjob central model")
    rep_delay = job_model.get("rep_delay", {}).get("seconds")
    if not _nonnegative(rep_delay):
        raise ValueError("Recorded explicit rep_delay seconds are required")
    pubs = job_model.get("pubs", [])
    if not pubs:
        raise ValueError("Complete PUB duration inventory required")
    pub_summaries = []
    native_parts, initialization_parts, repetition_parts = [], [], []
    expanded, executions = 0, 0
    for pub in pubs:
        count = pub.get("expanded_circuit_count")
        if type(count) is not int or count <= 0 or len(pub.get("timings", [])) != count:
            raise ValueError("Recorded PUB expansion inventory is invalid")
        shots = pub.get("shots_per_expanded_circuit") if shots_override is None else shots_override
        if type(shots) is not int or shots <= 0:
            raise ValueError("Recorded PUB shots must be positive integers")
        native = initialization = 0.0
        for timing in pub["timings"]:
            duration, reset = timing.get("circuit_seconds"), timing.get("initialization_seconds")
            if (
                not _nonnegative(duration)
                or not _nonnegative(reset)
                or timing.get("readout_included_once") is not True
                or timing.get("explicit_resets_included_in_circuit_seconds") is not True
                or timing.get("shots") != pub.get("shots_per_expanded_circuit")
            ):
                raise ValueError("Native, readout, reset or shot timing basis is inconsistent")
            native += shots * duration
            initialization += shots * reset
        repetition = count * shots * rep_delay
        native_parts.append(native)
        initialization_parts.append(initialization)
        repetition_parts.append(repetition)
        expanded += count
        executions += count * shots
        pub_summaries.append(
            {
                "source_pub_id": pub.get("pub_id"),
                "source_circuit_id": pub.get("circuit_id"),
                "source_qpy_sha256": pub.get("qpy_sha256"),
                "expanded_circuit_count": count,
                "source_shots_per_circuit": pub.get("shots_per_expanded_circuit"),
                "advisory_shots_per_circuit": shots,
                "executions": count * shots,
                "native_circuit_including_readout_seconds": native,
                "initialization_seconds": initialization,
                "rep_delay_seconds": repetition,
            }
        )
    if job_model.get("local_policy_subjobs") != expanded:
        raise ValueError("Local subjob scenario differs from the actual expanded circuit count")
    native = math.fsum(native_parts)
    initialization = math.fsum(initialization_parts)
    repetition = math.fsum(repetition_parts)
    shot_time = math.fsum((native, initialization, repetition))
    overhead, factor = 2.0 * expanded, float(default_policy.safety_factor)
    overhead_margin, shot_margin = (factor - 1) * overhead, (factor - 1) * shot_time
    risk = factor * (overhead + shot_time)
    if shots_override is None and (
        not math.isclose(shot_time, job_model["shot_time_seconds"], rel_tol=0, abs_tol=1e-12)
        or not math.isclose(risk, job_model["local_policy_seconds"], rel_tol=0, abs_tol=1e-12)
    ):
        raise ValueError("Source duration arithmetic differs from its recorded estimate")
    return {
        "source_job_id": job_model.get("job_id"),
        "units": "seconds",
        "pub_count": len(pubs),
        "expanded_circuit_count": expanded,
        "total_executions": executions,
        "shots_per_expanded_circuit": sorted(
            {row["advisory_shots_per_circuit"] for row in pub_summaries}
        ),
        "native_circuit_including_readout_seconds": native,
        "readout_counting": "Included once in native critical-path duration",
        "initialization_seconds": initialization,
        "rep_delay_seconds": repetition,
        "rep_delay_per_execution_seconds": rep_delay,
        "shot_time_seconds": shot_time,
        "central_subjobs": 1,
        "central_fixed_overhead_seconds": 2.0,
        "central_seconds": 2.0 + shot_time,
        "risk_subjobs": expanded,
        "risk_fixed_overhead_seconds": overhead,
        "risk_overhead_margin_seconds": overhead_margin,
        "risk_shot_margin_seconds": shot_margin,
        "risk_seconds": risk,
        "theoretical_zero_shot_risk_seconds": factor * overhead,
        "fixed_risk_alone_exceeds_active_job_cap": factor * overhead > 40,
        "active_job_cap_seconds": 40,
        "active_job_headroom_seconds": 40 - risk,
        "source_target_snapshot_sha256": job_model.get("target_snapshot_sha256"),
        "source_target_durations_sha256": job_model.get("target_durations_sha256"),
        "source_config_sha256": job_model.get("config_sha256"),
        "source_job_model_sha256": job_model.get("model_sha256"),
        "pub_summaries": pub_summaries,
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "campaign_job_count": len(rows),
        "total_pubs": sum(row["pub_count"] for row in rows),
        "total_expanded_circuits": sum(row["expanded_circuit_count"] for row in rows),
        "total_executions": sum(row["total_executions"] for row in rows),
        "central_total_seconds": math.fsum(row["central_seconds"] for row in rows),
        "risk_total_seconds": math.fsum(row["risk_seconds"] for row in rows),
        "fixed_risk_total_seconds": math.fsum(
            row["theoretical_zero_shot_risk_seconds"] for row in rows
        ),
        "job_summaries": rows,
    }


def compare_budget_variants(
    plan: Mapping[str, Any], model: Mapping[str, Any], *, source_head: str
) -> dict[str, Any]:
    """Validate the archival inputs, then generate exactly A and B advisory records."""
    if not isinstance(source_head, str) or not _FULL_COMMIT.fullmatch(source_head):
        raise ValueError("The full reviewed source HEAD is required")
    if plan.get("plan_hash") != plan_hash(plan):
        raise ValueError("Source execution-plan hash mismatch")
    validate_plan(plan, require_hardware=False)
    validate_usage_model(model, plan, require_ready=False)
    if plan.get("local_usage_model") != model or plan.get("usage_model_sha256") != model.get(
        "model_sha256"
    ):
        raise ValueError("Source execution plan and usage model binding differ")
    if tuple(job["id"] for job in plan["jobs"]) != _JOB_IDS:
        raise ValueError("The source requires its three fixed persistent job identities")
    original_rows = [summarize_job_time(job) for job in model["jobs"]]
    original = _summary(original_rows)
    active_caps = dict.fromkeys(_JOB_IDS, 40)
    original["budget_evaluation"] = evaluate_budget(
        {row["source_job_id"]: row["risk_seconds"] for row in original_rows},
        active_caps,
        total_cap_seconds=120,
    )
    a_rows = [
        summarize_job_time(job, shots_override=None if job["job_id"] == "smoke" else 512)
        for job in model["jobs"]
    ]
    a = _summary(a_rows)
    a_evaluation = evaluate_budget(
        {row["source_job_id"]: row["risk_seconds"] for row in a_rows},
        active_caps,
        total_cap_seconds=120,
    )
    a.update(
        variant="A",
        artifact_type="m5_advisory_shot_comparison",
        status="LOCAL_CHECKS_PASSED_REQUIRES_FRESH_PREFLIGHT"
        if a_evaluation["fits_all_limits"]
        else "BLOCKED",
        diagnostic_kind=a_evaluation["diagnostic_kind"],
        active_limits=_active_limits(),
        budget_evaluation=a_evaluation,
        reservation_proposal_seconds={"smoke": 40, "main-block-1": 40, "main-block-2": 40},
        campaign_reservation_proposal_seconds=120,
        executable=False,
        hardware_enabled=False,
        submission_approved=False,
        source_scientific_inventory_preserved=True,
    )
    b_rows = [summarize_job_time(job) for job in model["jobs"]]
    b = _summary(b_rows)
    b.update(
        variant="B",
        artifact_type="m5_advisory_budget_amendment",
        status="OWNER_BUDGET_APPROVAL_REQUIRED",
        active_limits=_active_limits(),
        proposed_reservations={
            "per_job_seconds": dict(_PROPOSED_CAPS),
            "campaign_seconds": 180,
            "max_campaign_submissions": 3,
            "max_concurrent_unresolved_own_jobs": 1,
            "max_cost_eur": 0,
            "free_open_plan_required": True,
            "approved": False,
            "is_provider_fact": False,
        },
        budget_evaluation=evaluate_budget(
            {row["source_job_id"]: row["risk_seconds"] for row in b_rows},
            _PROPOSED_CAPS,
            total_cap_seconds=180,
        ),
        executable=False,
        hardware_enabled=False,
        submission_approved=False,
        source_scientific_inventory_preserved=True,
    )
    if [a["total_executions"], b["total_executions"]] != [25088, 49664]:
        raise ValueError("Advisory shot counts differ from the two explicitly requested variants")
    payload = {
        "schema_version": 1,
        "artifact_type": "m5_advisory_budget_comparison",
        "status": "ADVISORY_ONLY",
        "source_bindings": {
            "source_head": source_head,
            "source_plan_hash": plan["plan_hash"],
            "source_usage_model_sha256": model["model_sha256"],
            "source_source_context_sha256": plan["source_context_sha256"],
            "source_prepared_manifest_sha256": plan["prepared_manifest_sha256"],
            "source_selection_manifest_sha256": plan["selection_manifest_sha256"],
            "source_target_snapshot_sha256": plan["target_snapshot_sha256"],
            "source_qpy_hashes": {
                identity: row["qpy_sha256"] for identity, row in plan["circuits"].items()
            },
        },
        "backend": plan.get("backend") or plan.get("offline_backend_fixture"),
        "active_limits": _active_limits(),
        "original_summary": original,
        "variants": [a, b],
        "theoretical_zero_shot_campaign_risk_seconds": original["fixed_risk_total_seconds"],
        "theoretical_zero_shot_is_valid_hardware_plan": False,
        "shot_reduction_alone_can_fit_active_default_risk_policy": original[
            "fixed_risk_total_seconds"
        ]
        <= 120
        and all(row["theoretical_zero_shot_risk_seconds"] <= 40 for row in original_rows),
        "risk_policy": dict(model["jobs"][0]["local_policy"]),
        "subjob_assumption": (
            "One internal subjob per expanded circuit is a local risk scenario; "
            "the recorded central model uses one per Runtime job"
        ),
        "is_provider_quote": False,
        "is_guaranteed_upper_bound": False,
        "provider_account_refreshed": False,
        "measured_hardware_usage_seconds": None,
        "executable": False,
        "hardware_enabled": False,
        "submission_approved": False,
        "uncertainties": list(model["jobs"][0]["uncertainties"]),
        "fresh_preflight_required_before_any_executable_plan": True,
    }
    payload["draft_sha256"] = canonical_hash(payload)
    return payload


def validate_budget_comparison(
    artifact: Mapping[str, Any], plan: Mapping[str, Any], model: Mapping[str, Any]
) -> None:
    """Check the advisory seal and reproduce it from the original bound inputs."""
    if artifact.get("draft_sha256") != canonical_hash(
        {key: value for key, value in artifact.items() if key != "draft_sha256"}
    ):
        raise ValueError("Advisory comparison draft seal mismatch")
    expected = compare_budget_variants(
        plan, model, source_head=artifact.get("source_bindings", {}).get("source_head", "")
    )
    if artifact != expected:
        raise ValueError("Advisory comparison differs from its frozen source inputs")
