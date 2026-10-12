"""Immutable execution-plan binding and explicit human approval validation."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from km_quantum.m5.provider import (
    SafetyError,
    parse_time,
    utc_now,
    validate_preflight,
    validate_runtime_options,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def plan_hash(plan: Mapping[str, Any]) -> str:
    value = {key: item for key, item in plan.items() if key not in {"plan_hash", "plan_sha256"}}
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_plan(plan: Mapping[str, Any], *, require_hardware: bool = True) -> None:
    """The initial study cannot silently grow or drop required jobs/PUBs."""
    if plan.get("hardware_enabled") is not False or plan.get("submission_approved") is not False:
        raise SafetyError("Frozen plan flags must remain false until validated execution approval")
    if require_hardware and (
        plan.get("artifact_type") != "m5_hardware_execution_plan"
        or plan.get("provider_verified") is not True
        or plan.get("source_kind") != "ibm_hardware"
        or not plan.get("backend")
        or not _SHA256.fullmatch(str(plan.get("instance_binding", "")))
    ):
        raise SafetyError("A concrete verified hardware execution plan is required")
    if "plan_hash" in plan and plan["plan_hash"] != plan_hash(plan):
        raise SafetyError("Execution plan hash is inconsistent")
    if "plan_sha256" in plan and plan["plan_sha256"] != plan_hash(plan):
        raise SafetyError("Execution plan hash is inconsistent")
    budget = plan.get("budget", {})
    expected = {
        "max_provider_jobs": 3,
        "max_total_shots": 49664,
        "max_qpu_seconds_per_job": 40,
        "max_total_qpu_seconds": 120,
        "max_cost_eur": 0,
    }
    if budget != expected:
        raise SafetyError("This implementation requires the fixed reviewed M5 budget")
    validate_runtime_options(plan.get("runtime_options", {}))
    if plan["runtime_options"]["max_execution_time"] != 40:
        raise SafetyError("Every job must reserve the full 40-second cap")
    jobs = plan.get("jobs", [])
    if len(jobs) != 3 or [job.get("kind") for job in jobs] != ["smoke", "main", "main"]:
        raise SafetyError("Exactly smoke then two main jobs are required")
    if len({job.get("id") for job in jobs}) != 3 or any(not job.get("id") for job in jobs):
        raise SafetyError("Persistent unique job IDs are required")
    circuits = plan.get("circuits", {})
    if not isinstance(circuits, dict) or not circuits or not plan.get("layout"):
        raise SafetyError("Frozen QPY hashes and layout are required")
    pub_ids: list[str] = []
    shot_sum = 0
    for job in jobs:
        if job.get("max_execution_time") != 40:
            raise SafetyError("Each job requires its approved QPU cap")
        shots = 128 if job["kind"] == "smoke" else 1024
        expected_pubs = 4 if job["kind"] == "smoke" else 24
        if len(job.get("pubs", [])) != expected_pubs:
            raise SafetyError("Required PUB count differs from the frozen study")
        local_ids: set[str] = set()
        for pub in job["pubs"]:
            circuit_id = pub.get("circuit_id")
            identity = pub.get("pub_id")
            if not isinstance(identity, str) or not identity or identity in local_ids:
                raise SafetyError("Persistent unique PUB IDs are required")
            local_ids.add(identity)
            pub_ids.append(identity)
            if pub.get("shots") != shots or type(pub.get("shots")) is not int:
                raise SafetyError("Shots differ from the frozen study")
            if type(pub.get("num_bits")) is not int or not 1 <= pub["num_bits"] <= 10:
                raise SafetyError("PUB register width is missing or exceeds the guard")
            if circuit_id not in circuits:
                raise SafetyError("PUB refers to an unknown frozen circuit")
            circuit_hash = circuits[circuit_id].get("qpy_sha256")
            if not _SHA256.fullmatch(str(circuit_hash)) or pub.get("circuit_hash") != circuit_hash:
                raise SafetyError("PUB circuit hash differs from frozen QPY")
            shot_sum += shots
    if len(set(pub_ids)) != 52 or shot_sum != 49664:
        raise SafetyError("Total PUB IDs or shots differ from the frozen study")
    if require_hardware:
        from km_quantum.m5.provider_limits import (
            LimitContractError,
            service_limit_policy,
            validate_limit_assessment,
        )
        from km_quantum.m5.usage import validate_usage_model

        try:
            validate_limit_assessment(plan, service_limit_policy())
            validate_usage_model(plan.get("local_usage_model", {}), plan)
        except (LimitContractError, KeyError, TypeError, ValueError) as error:
            raise SafetyError(
                f"Concrete execution contracts are invalid: {error}",
                diagnostic_kind="PROVIDER_CONTRACT_ERROR",
            ) from None


def approval_request(plan: Mapping[str, Any], expires_at: str) -> dict[str, Any]:
    """Generate an unsigned proposal; this function can never approve it."""
    parse_time(expires_at)
    return {
        "artifact_type": "m5_human_approval",
        "schema_version": 1,
        "plan_hash": plan_hash(plan),
        "approved": False,
        "signed_by": None,
        "signed_at": None,
        "expires_at": expires_at,
        "backend": plan.get("backend"),
        "instance_binding": plan.get("instance_binding"),
        "layout": copy.deepcopy(plan.get("layout")),
        "circuits": copy.deepcopy(plan.get("circuits")),
        "jobs": copy.deepcopy(plan.get("jobs")),
        "budget": copy.deepcopy(plan.get("budget")),
        "runtime_options": copy.deepcopy(plan.get("runtime_options")),
        "hardware_enabled": False,
        "submission_approved": False,
        "approval_method": "Florian manually reviews and edits this local file",
    }


def validate_human_approval(
    plan: Mapping[str, Any],
    approval: Mapping[str, Any],
    *,
    now: str | None = None,
) -> None:
    """Validate every human binding locally before credential or provider access."""
    validate_plan(plan)
    if (
        approval.get("artifact_type") != "m5_human_approval"
        or approval.get("schema_version") != 1
        or approval.get("approved") is not True
        or approval.get("signed_by") != "Florian"
        or approval.get("plan_hash") != plan_hash(plan)
    ):
        raise SafetyError("Exact execution plan requires explicit approval signed by Florian")
    current = parse_time(now or utc_now())
    signed = parse_time(approval.get("signed_at", ""))
    expiry = parse_time(approval.get("expires_at", ""))
    if signed > current or expiry <= current or signed >= expiry:
        raise SafetyError("Human approval is expired or has invalid dates")
    if expiry - signed > timedelta(hours=24):
        raise SafetyError("Human approval validity may not exceed 24 hours")
    for key in (
        "backend",
        "instance_binding",
        "layout",
        "circuits",
        "jobs",
        "budget",
        "runtime_options",
    ):
        if approval.get(key) != plan.get(key):
            raise SafetyError("Human approval differs from the frozen execution plan")


def validate_approval(
    plan: Mapping[str, Any],
    approval: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    now: str | None = None,
) -> None:
    validate_human_approval(plan, approval, now=now)
    validate_preflight(evidence, plan, now=now)
