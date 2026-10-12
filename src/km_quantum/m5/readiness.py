"""Read-only M5 acceptance, separating offline readiness from real hardware.

No builder flag can authorize a job or turn mock counts into hardware.  The
scientific agreement itself is descriptive; technical identity, provenance and
complete required data determine acceptance.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

from km_quantum.m4.io import canonical_hash, lf_file_hash, read_json
from km_quantum.m5.approvals import plan_hash, validate_plan
from km_quantum.m5.io import historical_integrity, raw_hash, read_sealed
from km_quantum.m5.ledger import JobLedger, validate_counts
from km_quantum.m5.manifest import source_context
from km_quantum.m5.provider import parse_time, redact
from km_quantum.m5.workflow import read_prepared_selection, verify_plan_inventory_binding

_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_TOOLING = ("pytest", "ruff_check", "ruff_format", "mypy", "build")
_DIAGNOSTIC_KINDS = {
    "OWNER_CONFIGURATION_REQUIRED",
    "PROVIDER_CONTRACT_ERROR",
    "USAGE_LIMIT_EXCEEDED",
    "INSUFFICIENT_USAGE_MODEL",
    "LOCAL_CHANNEL_OR_SIMULATION_ERROR",
    "MISSING_REAL_CALIBRATION",
    "LOCAL_IMPLEMENTATION_ERROR",
}


def _require(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _artifact_path(root: Path, output: Path, value: str) -> Path:
    path = (root / value).resolve()
    _require(path.is_relative_to(output.resolve()), "Artifact path escaped M5 output")
    return path


def _verify_inventory(root: Path, output: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    prepared = read_sealed(output / "prepared_manifest.json")
    local = read_sealed(output / "local_verification.json")
    source = prepared.get("source_kind")
    _require(source in {"offline_synthetic", "offline_test_backend", "provider"}, "Unknown source")
    _require(prepared.get("hardware_used") is False, "Preparation cannot contain hardware results")
    _require(
        prepared.get("source_context_sha256") == source_context(root)["content_sha256"],
        "Implementation changed after preparation",
    )
    selection = read_prepared_selection(root, output, prepared)
    _require(
        selection["content_sha256"] == prepared.get("selection_manifest_sha256"),
        "Selection manifest changed",
    )
    _require(
        selection.get("protocol_lf_sha256")
        == lf_file_hash(root / "docs/m5_experiment_protocol.md"),
        "Precommitted protocol changed",
    )
    _require(
        selection.get("work_order_sha256")
        == raw_hash(root / "KM3_M5_IBM_Hardware_Validation_Prompt.md"),
        "M5 work order changed",
    )
    snapshot = read_sealed(output / "target_snapshot.json")
    _require(snapshot["content_sha256"] == prepared["target_snapshot_sha256"], "Snapshot changed")
    _require(snapshot.get("source") == source, "Snapshot provenance differs")
    records = prepared.get("records", [])
    ids = [record["circuit_id"] for record in records]
    _require(len(records) == len(set(ids)) == 28, "Required circuit inventory is incomplete")
    _require(sum(record["kind"] == "main" for record in records) == 24, "Missing main circuits")
    _require(sum(record["kind"] == "smoke" for record in records) == 4, "Missing smoke circuits")
    for record in records:
        identity = record["circuit_id"]
        _require(
            bool(_ID.fullmatch(identity)) and identity not in {".", ".."}, "Invalid circuit ID"
        )
        _require(
            read_sealed(output / "circuits" / identity / "record.json") == record,
            "Circuit record differs from inventory",
        )
        _require(set(record["qpy"]) == {"logical", "provider", "compact"}, "Incomplete QPY set")
        for metadata in record["qpy"].values():
            path = _artifact_path(root, output, metadata["path"])
            _require(raw_hash(path) == metadata["sha256"], "Frozen QPY bytes changed")
        _require(record["equivalence"].get("passed") is True, "Compilation equivalence failed")
        _require(record["summary"].get("active_width", 11) <= 10, "Active width exceeds guard")
        prediction = read_sealed(output / "predictions" / f"{identity}.json")
        _require(prediction["content_sha256"] == record["prediction_sha256"], "Prediction changed")
        _require(prediction.get("circuit_id") == identity, "Prediction circuit identity differs")
        _require(prediction.get("source_kind") == source, "Prediction source differs")
        _require(prediction.get("hardware_used") is False, "Prediction is not hardware data")
        _require(prediction.get("frozen_before_results") is True, "Prediction was not frozen")
        _require(
            prediction.get("target_snapshot_sha256") == snapshot["content_sha256"],
            "Prediction refers to another snapshot",
        )
        noise = prediction["device_noise"]
        expected_noise_status = "READY" if source == "provider" else "OFFLINE_READY"
        _require(
            record.get("device_noise_status") == expected_noise_status, "Device noise is blocked"
        )
        _require(noise.get("status") == expected_noise_status, "Prediction noise status differs")
        _require(noise.get("coverage", {}).get("verified") is True, "Noise coverage is missing")
        _require(noise.get("readout_applications") == 1, "Readout was not applied exactly once")
        _require(noise.get("additional_m4_depolarization") is False, "Double gate noise")
        _require(
            noise.get("compact_to_physical") == list(record["mapping"]["active_physical_qubits"]),
            "Physical noise attachment mapping differs",
        )
        _require(
            noise.get("measured_compact_by_m2") == list(record["mapping"]["m2_to_compact"]),
            "Readout measurement mapping differs",
        )
        width = record["num_bits"]
        _require(type(width) is int and 1 <= width <= 7, "Logical width exceeds frozen suite")
        for values in (
            prediction["logical_ideal"],
            prediction["compiled_ideal"],
            noise["probabilities_before_readout"],
            noise["probabilities_after_readout"],
        ):
            vector = np.asarray(values, dtype=float)
            _require(
                vector.shape == (2**width,)
                and np.all(np.isfinite(vector))
                and np.all(vector >= 0)
                and np.isclose(vector.sum(), 1, rtol=0, atol=1e-9),
                "Invalid frozen logical probability vector",
            )
        _require(
            np.allclose(
                prediction["logical_ideal"], prediction["compiled_ideal"], rtol=0, atol=1e-10
            ),
            "Ideal hardware compilation changed distribution",
        )
    _require(local.get("status") == "PASS", "Local verification is not PASS")
    _require(
        local.get("prepared_manifest_sha256") == prepared["content_sha256"], "Stale verification"
    )
    _require(local.get("source_kind") == source, "Verification source differs")
    checks = local.get("checks", [])
    main_ids = {record["circuit_id"] for record in records if record["kind"] == "main"}
    _require(
        len(checks) == 24 and {row["circuit_id"] for row in checks} == main_ids, "Missing checks"
    )
    _require(all(row.get("passed") is True for row in checks), "A compilation check failed")
    _require(local.get("qpy_round_trip_verified") is True, "QPY round-trip is unverified")
    _require(local.get("bit_mapping_verified") is True, "Bit mapping is unverified")
    _require(local.get("active_width_maximum", 11) <= 10, "Verified active width exceeds guard")
    delta = local.get("maximum_probability_absolute_difference")
    _require(
        isinstance(delta, (int, float)) and np.isfinite(delta) and 0 <= delta <= 1e-10,
        "Local ideal probability tolerance failed",
    )
    _require(
        local.get("historical_integrity", {}).get("status") == "PASS", "Recorded history failed"
    )
    return prepared, local


def _verify_plan(plan: dict[str, Any], prepared: dict[str, Any]) -> None:
    validate_plan(plan, require_hardware=prepared["source_kind"] == "provider")
    _require(plan.get("plan_hash", plan.get("plan_sha256")) == plan_hash(plan), "Missing plan hash")
    verify_plan_inventory_binding(plan, prepared)


def _verify_ledger(ledger: dict[str, Any], plan: dict[str, Any]) -> None:
    if plan.get("budget_authorization") is not None:
        JobLedger._check_binding(ledger, plan)
    jobs = ledger.get("jobs", [])
    _require(ledger.get("plan_hash") in {None, plan_hash(plan)}, "Ledger plan differs")
    _require(len(jobs) <= 3, "Too many ledger reservations")
    _require(
        [record["job_id"] for record in jobs] == [job["id"] for job in plan["jobs"][: len(jobs)]],
        "Ledger order or job identity differs",
    )
    provider_ids = [record["provider_job_id"] for record in jobs if record.get("provider_job_id")]
    _require(len(provider_ids) == len(set(provider_ids)), "Duplicate provider IDs")
    for record, job in zip(jobs, plan["jobs"], strict=False):
        _require(
            record.get("status")
            in {"SUBMISSION_UNKNOWN", "SUBMITTED", "COLLECTED", "PARTIAL", "FAILED", "CANCELLED"},
            "Unknown ledger state",
        )
        _require(record.get("plan_hash") == plan_hash(plan), "Job plan differs")
        _require(record.get("pubs") == job["pubs"], "Job PUB identities differ")
        _require(
            record.get("reserved_qpu_seconds") == job["max_execution_time"],
            "Job reservation differs",
        )
    _require(
        ledger.get("reserved_qpu_seconds")
        == sum(job["max_execution_time"] for job in plan["jobs"][: len(jobs)]),
        "Reservation total differs",
    )
    _require(
        ledger.get("reserved_shots")
        == sum(pub["shots"] for record in jobs for pub in record["pubs"]),
        "Shot reservation total differs",
    )


def _approval_evidence(record: Mapping[str, Any], plan: dict[str, Any]) -> bool:
    evidence = record.get("approval_evidence", {})
    if (
        evidence.get("approved") is not True
        or evidence.get("signed_by") != "Florian"
        or evidence.get("plan_hash") != plan_hash(plan)
        or not _SHA.fullmatch(str(evidence.get("approval_sha256", "")))
        or not _SHA.fullmatch(str(evidence.get("preflight_sha256", "")))
        or record.get("source_kind") != "ibm_hardware"
        or record.get("single_job_contract") is not True
    ):
        return False
    try:
        signed, validated, expires = (
            parse_time(evidence[key]) for key in ("signed_at", "validated_at", "expires_at")
        )
    except (ValueError, KeyError, TypeError):
        return False
    return signed <= validated < expires


def _tooling_status(tooling: dict[str, Any] | None) -> str:
    if tooling is None or any(key not in tooling for key in _TOOLING):
        return "LOCAL_CHECKS_PENDING"
    statuses = [
        value.get("status") if isinstance(value, Mapping) else value
        for value in (tooling[key] for key in _TOOLING)
    ]
    return "PASS" if all(status is True or status == "PASS" for status in statuses) else "FAIL"


def _usage_seconds(record: Mapping[str, Any]) -> float | None:
    metrics = record.get("provider_metrics")
    if not isinstance(metrics, Mapping) or not isinstance(metrics.get("usage"), Mapping):
        return None
    value = metrics["usage"].get("quantum_seconds")
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not np.isfinite(value)
        or value < 0
    ):
        return None
    return float(value)


def _usage_budget_diagnostics(
    jobs: list[dict[str, Any]],
    usage_seconds: list[float | None],
    plan: dict[str, Any] | None,
) -> list[str]:
    """Charge measured quantum usage against each reviewed cap and the campaign."""
    diagnostics = []
    caps = {job["id"]: job["max_execution_time"] for job in plan["jobs"]} if plan else {}
    if any(
        value is not None and value > caps.get(job.get("job_id"), 0)
        for job, value in zip(jobs, usage_seconds, strict=True)
    ):
        diagnostics.append("provider_usage: actual QPU usage exceeds the reviewed per-job cap")
    if (
        plan is not None
        and sum(value for value in usage_seconds if value is not None)
        > plan["budget"]["max_total_qpu_seconds"]
    ):
        diagnostics.append("provider_usage: actual QPU usage exceeds the reviewed campaign cap")
    return diagnostics


def _reported_draft(output: Path, prepared: dict[str, Any] | None) -> dict[str, Any] | None:
    """Diagnostic inventory only; never accepted as an executable hardware plan."""
    if prepared is None:
        return None
    path = output / "execution_plan.json"
    if not path.exists():
        path = output / "provider_execution_draft.json"
    try:
        candidate = read_json(path)
        _require(isinstance(candidate, dict), "Invalid diagnostic draft")
        validate_plan(candidate, require_hardware=False)
        _require(candidate.get("plan_hash") == plan_hash(candidate), "Draft seal differs")
        verify_plan_inventory_binding(candidate, prepared)
    except (ValueError, KeyError, TypeError, OSError, ImportError):
        return None
    return candidate


def _cached_preflight(output: Path) -> dict[str, Any]:
    """Allowlisted historical facts; reading them never renews provider evidence."""
    path = output / "provider_preflight.json"
    if not path.exists():
        return {}
    try:
        cached = read_json(path)
        _require(isinstance(cached, dict), "Invalid cached preflight")
        parse_time(cached.get("obtained_at", ""))
    except (ValueError, KeyError, TypeError, OSError):
        return {}
    observation = {
        key: cached.get(key)
        for key in (
            "status",
            "source_kind",
            "provider_verified",
            "backend",
            "instance_binding",
            "plan_hash",
            "obtained_at",
            "plan",
            "pricing_type",
            "cost_eur",
            "operational",
            "simulator",
            "fake",
        )
    }
    kind = cached.get("diagnostic_kind")
    observation["diagnostic_kind"] = (
        kind if isinstance(kind, str) and kind in _DIAGNOSTIC_KINDS else None
    )
    # Credential-bearing free text, SDK details, headers and complete service
    # objects are deliberately absent from this diagnostic observation.
    for group, keys in (
        (
            "usage",
            ("remaining_seconds", "consumed_seconds", "limit_seconds", "usage_limit_reached"),
        ),
        ("pending_jobs", ("complete", "count", "status")),
    ):
        value = cached.get(group, {})
        observation[group] = (
            {key: value.get(key) for key in keys} if isinstance(value, Mapping) else {}
        )
    observation.update(
        artifact="provider_preflight.json",
        historical_snapshot=True,
        live_read_performed=False,
        fresh_submission_eligibility_claimed=False,
    )
    return dict(redact(observation))


def build_acceptance(
    root: Path, output: Path, tooling: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Read existing artifacts without writing state, accessing IBM, or simulating."""
    root = root.resolve()
    output = (root / output).resolve() if not output.is_absolute() else output.resolve()
    diagnostics: list[str] = []
    implementation = _tooling_status(tooling)
    integrity: dict[str, Any] = {"status": "NOT_AVAILABLE"}
    prepared: dict[str, Any] | None = None
    local: dict[str, Any] | None = None
    plan: dict[str, Any] | None = None
    ledger: dict[str, Any] = {"jobs": [], "reserved_qpu_seconds": 0, "reserved_shots": 0}
    collected: dict[str, Any] | None = None
    analysis: dict[str, Any] | None = None
    for stage in ("history", "inventory", "plan", "ledger", "collected", "analysis"):
        try:
            if stage == "history":
                integrity = historical_integrity(root)
                _require(integrity.get("status") == "PASS", "Historical content changed")
            elif stage == "inventory":
                prepared, local = _verify_inventory(root, output)
            elif stage == "plan":
                candidate = read_json(output / "execution_plan.json")
                _require(isinstance(candidate, dict), "Invalid execution plan")
                if prepared is None:
                    raise ValueError("Inventory is unavailable")
                _verify_plan(candidate, prepared)
                plan = candidate
            elif stage == "ledger" and plan is not None:
                candidate = JobLedger(output / "jobs" / "ledger.json").snapshot()
                _verify_ledger(candidate, plan)
                ledger = candidate
            elif stage == "collected" and (output / "raw" / "collected.json").exists():
                candidate = read_json(output / "raw" / "collected.json")
                _require(isinstance(candidate, dict), "Invalid collected results")
                collected = candidate
            elif stage == "analysis" and (output / "summaries" / "analysis.json").exists():
                candidate = read_sealed(output / "summaries" / "analysis.json")
                _require(isinstance(candidate, dict), "Invalid analysis")
                analysis = candidate
        except (ValueError, KeyError, TypeError, OSError, ImportError) as error:
            diagnostics.append(f"{stage}: {type(error).__name__}; artifact gate failed")
    # Preserve structurally bound planned quantities without accepting a
    # blocked draft into the hardware-plan/ledger/approval validation path.
    diagnostic_plan = plan if plan is not None else _reported_draft(output, prepared)
    observation = _cached_preflight(output)
    diagnostic_kind = observation.get("diagnostic_kind") or (
        diagnostic_plan.get("diagnostic_kind") if diagnostic_plan else None
    )
    if not isinstance(diagnostic_kind, str) or diagnostic_kind not in _DIAGNOSTIC_KINDS:
        diagnostic_kind = None
    diagnostic_details = []
    if observation.get("status") in {"BLOCKED", "OWNER_CONFIGURATION_REQUIRED"}:
        diagnostics.append(
            "provider_preflight: "
            + (diagnostic_kind or "UNKNOWN_DIAGNOSTIC")
            + "; cached blocked observation"
        )
        diagnostic_details.append(
            {
                "stage": "provider_preflight",
                "kind": diagnostic_kind,
                "evidence_scope": "historical_snapshot",
                "live_read_performed": False,
            }
        )
    jobs = ledger.get("jobs", [])
    planned_jobs = diagnostic_plan.get("jobs", []) if diagnostic_plan else []
    pubs = {pub["pub_id"]: pub for job in planned_jobs for pub in job["pubs"]}
    raw = collected.get("raw_results", []) if collected else []
    real_ids: set[str] = set()
    all_ids: set[str] = set()
    try:
        if collected:
            _require(
                plan is not None and collected.get("plan_hash") == plan_hash(plan),
                "Raw plan differs",
            )
            _require(collected.get("job_records") == jobs, "Raw job records differ from ledger")
        ledger_raw = [row for job in jobs for row in job.get("raw_results", [])]
        _require(raw == ledger_raw, "Collected raw data differs from persistent ledger")
        for row in raw:
            identity = row["pub_id"]
            _require(identity in pubs and identity not in all_ids, "Unknown or duplicate raw PUB")
            pub = pubs[identity]
            for key in ("circuit_id", "circuit_hash", "shots", "num_bits"):
                _require(row.get(key) == pub[key], "Raw PUB identity differs")
            validate_counts(row["counts"], pub["num_bits"], pub["shots"])
            all_ids.add(identity)
            if row.get("source") == "hardware" and row.get("hardware_used") is True:
                real_ids.add(identity)
            else:
                _require(
                    row.get("source") == "mock" and row.get("hardware_used") is False,
                    "Bad provenance",
                )
        if real_ids:
            _require(
                prepared is not None
                and prepared.get("source_kind") == "provider"
                and plan is not None
                and plan.get("source_kind") == "ibm_hardware"
                and plan.get("provider_verified") is True,
                "Real counts require the reviewed provider preparation and plan",
            )
    except (ValueError, KeyError, TypeError) as error:
        diagnostics.append(f"raw_contract: {type(error).__name__}; artifact gate failed")
    statuses = [job.get("status") for job in jobs]
    approved = (
        bool(jobs) and plan is not None and all(_approval_evidence(job, plan) for job in jobs)
    )
    approval_status = "APPROVED_EVIDENCE_VERIFIED" if approved else "NOT_APPROVED"
    if jobs and not approved:
        approval_status = "INVALID_OR_MISSING_EVIDENCE"
    complete_jobs = sum(
        job.get("status") == "COLLECTED"
        and job.get("contract_verified") is True
        and job.get("source_kind") == "ibm_hardware"
        and job.get("hardware_used") is True
        for job in jobs
    )
    complete_real = len(pubs) == len(real_ids) == 52 and complete_jobs == 3
    hardware_used = bool(real_ids)
    analysis_status = (
        "NOT_AVAILABLE" if analysis is None else analysis.get("analysis_status", "INVALID")
    )
    analysis_complete = False
    if analysis is not None:
        try:
            if prepared is None or plan is None or collected is None:
                raise ValueError("Analysis inputs missing")
            _require(analysis.get("plan_hash") == plan_hash(plan), "Analysis plan differs")
            _require(
                analysis.get("prepared_manifest_sha256") == prepared["content_sha256"],
                "Analysis inventory differs",
            )
            _require(
                analysis.get("collected_content_sha256") == canonical_hash(collected),
                "Analysis raw input differs",
            )
            main_ids = {
                pub["pub_id"]
                for job in planned_jobs
                if job["kind"] == "main"
                for pub in job["pubs"]
            }
            rows = [row for block in analysis.get("blocks", {}).values() for row in block]
            _require(len(rows) == len({row["pub_id"] for row in rows}), "Duplicate analyzed PUB")
            _require(all(row["pub_id"] in main_ids for row in rows), "Unknown analyzed PUB")
            if analysis_status == "ANALYZED":
                _require(set(analysis.get("blocks", {})) == {"1", "2"}, "Repeat blocks missing")
                for block, job in enumerate(planned_jobs[1:], start=1):
                    _require(
                        {row["pub_id"] for row in analysis["blocks"][str(block)]}
                        == {pub["pub_id"] for pub in job["pubs"]},
                        "Analyzed repeat blocks were mixed",
                    )
            analysis_complete = (
                analysis_status == "ANALYZED"
                and analysis.get("hardware_used") is True
                and analysis.get("blocks_pooled") is False
                and analysis.get("analyzed_pub_count") == 48
                and analysis.get("expected_pub_count") == 48
                and analysis.get("real_hardware_pub_count") == 48
                and analysis.get("missing_pub_ids") == []
                and {row["pub_id"] for row in rows} == main_ids
                and all(
                    row.get("hardware_used") is True and row.get("source") == "hardware"
                    for row in rows
                )
            )
        except (ValueError, KeyError, TypeError) as error:
            diagnostics.append(f"analysis_binding: {type(error).__name__}; artifact gate failed")
            analysis_status = "INVALID"
    usage_seconds = [_usage_seconds(job) for job in jobs]
    usage_complete = bool(jobs) and all(value is not None for value in usage_seconds)
    diagnostics.extend(_usage_budget_diagnostics(jobs, usage_seconds, diagnostic_plan))
    local_ready = prepared is not None and local is not None and integrity.get("status") == "PASS"
    if diagnostics or not local_ready or implementation != "PASS":
        status = "BLOCKED"
    elif "SUBMISSION_UNKNOWN" in statuses:
        status = "SUBMISSION_UNKNOWN"
    elif "FAILED" in statuses:
        status = "FAILED"
    elif "CANCELLED" in statuses:
        status = "CANCELLED"
    elif complete_real:
        status = "PASS" if approved and analysis_complete and usage_complete else "PARTIAL"
    elif hardware_used or "PARTIAL" in statuses:
        status = "PARTIAL"
    elif jobs and any(job.get("provider_job_id") for job in jobs) and not raw:
        status = "PENDING_HARDWARE"
    else:
        status = "OFFLINE_READY"
    if "SUBMISSION_UNKNOWN" in statuses:
        execution = "SUBMISSION_UNKNOWN"
    elif "FAILED" in statuses:
        execution = "FAILED"
    elif "CANCELLED" in statuses:
        execution = "CANCELLED"
    elif complete_real:
        execution = "COLLECTED"
    elif hardware_used or "PARTIAL" in statuses:
        execution = "PARTIAL"
    elif raw:
        execution = "TEST_DATA_ONLY"
    elif jobs:
        execution = "PENDING_HARDWARE"
    else:
        execution = "NOT_STARTED"
    source = prepared.get("source_kind") if prepared else None
    configuration_status = (
        "PROVIDER_PREFLIGHT_READY"
        if source == "provider" and plan
        else "OWNER_CONFIGURATION_REQUIRED"
    )
    if observation:
        binding_matches = (
            diagnostic_plan is not None
            and all(
                observation.get(key) == diagnostic_plan.get(key)
                for key in ("backend", "instance_binding")
            )
            and observation.get("plan_hash") == plan_hash(diagnostic_plan)
        )
        authenticated_facts = (
            observation.get("provider_verified") is True
            and observation.get("source_kind") == "ibm_hardware"
            and observation.get("plan") == "open"
            and observation.get("pricing_type") == "free"
            and observation.get("cost_eur") == 0
            and observation.get("operational") is True
            and observation.get("simulator") is False
            and observation.get("fake") is False
        )
        configuration_status = (
            "VERIFIED_HISTORICAL"
            if authenticated_facts and binding_matches
            else "HISTORICAL_PROVIDER_OBSERVATION_UNBOUND"
            if authenticated_facts
            else "OWNER_CONFIGURATION_REQUIRED"
        )
    result = {
        "schema_version": 1,
        "artifact_type": "m5_acceptance",
        "status": status,
        "implementation_status": implementation,
        "historical_integrity": integrity,
        "local_compilation_equivalence": "PASS" if local_ready else "BLOCKED",
        "bit_mapping_verified": bool(local and local.get("bit_mapping_verified") is True),
        "local_verification_summary": {
            "checked_main_circuits": len(local.get("checks", [])) if local else 0,
            "maximum_probability_absolute_difference": (
                local.get("maximum_probability_absolute_difference") if local else None
            ),
            "active_width_maximum": local.get("active_width_maximum") if local else None,
        },
        "device_noise_model_status": (
            "READY"
            if source == "provider"
            else "OFFLINE_SYNTHETIC_READY"
            if prepared
            else "BLOCKED"
        ),
        "provider_configuration_status": configuration_status,
        "provider_preflight_observation": observation,
        "diagnostic_kind": diagnostic_kind,
        "gate_diagnostic_details": diagnostic_details,
        "plan_diagnostics": {
            "artifact_type": diagnostic_plan.get("artifact_type") if diagnostic_plan else None,
            "status": diagnostic_plan.get("status") if diagnostic_plan else None,
            "plan_hash": plan_hash(diagnostic_plan) if diagnostic_plan else None,
            "inventory_binding_verified": diagnostic_plan is not None,
            "hardware_execution_valid": plan is not None,
        },
        "approval_status": approval_status,
        "planned_provider_jobs": len(planned_jobs),
        "submitted_provider_jobs": sum(
            bool(job.get("provider_job_id")) and job.get("source_kind") == "ibm_hardware"
            for job in jobs
        ),
        "completed_provider_jobs": complete_jobs,
        "completed_test_jobs": sum(
            job.get("status") == "COLLECTED" and job.get("source_kind") == "mock" for job in jobs
        ),
        "missing_provider_jobs": len(planned_jobs) - complete_jobs,
        "planned_pubs": len(pubs),
        "planned_main_pubs": sum(len(job["pubs"]) for job in planned_jobs if job["kind"] == "main"),
        "planned_smoke_pubs": sum(
            len(job["pubs"]) for job in planned_jobs if job["kind"] == "smoke"
        ),
        "submitted_pubs": sum(
            len(job["pubs"])
            for job in jobs
            if job.get("provider_job_id") and job.get("source_kind") == "ibm_hardware"
        ),
        "completed_real_pubs": len(real_ids),
        "test_data_pubs": len(all_ids - real_ids),
        "missing_pubs": len(pubs) - len(real_ids),
        "missing_pub_ids": sorted(set(pubs) - real_ids),
        "hardware_execution_status": execution,
        "hardware_used": hardware_used,
        "raw_results_complete": complete_real,
        "analysis_status": analysis_status,
        "observed_hardware_agreement": (
            analysis.get("observed_hardware_agreement", "NOT_AVAILABLE")
            if hardware_used and analysis_complete and analysis
            else "NOT_AVAILABLE"
        ),
        "good_prediction_agreement_required_for_pass": False,
        "budget_evidence": {
            "reserved_qpu_seconds": ledger.get("reserved_qpu_seconds", 0),
            "reserved_shots": ledger.get("reserved_shots", 0),
            "actual_provider_metrics_available": usage_complete,
            "actual_qpu_seconds_per_job": usage_seconds,
            "actual_total_qpu_seconds": sum(value for value in usage_seconds if value is not None)
            if usage_complete
            else None,
            "provider_metrics": [job.get("provider_metrics") for job in jobs],
            "qpu_time_is_queue_or_wall_time": False,
            "euro_cost_guarantee_claimed": False,
        },
        "limitations": [
            "Offline/fake data establish only local readiness, never real hardware completion",
            "Backend-derived noise is approximate; idle schedules, crosstalk "
            "and drift are unmodelled",
            "Known four-case suite and two repeat blocks do not establish Warm-Start superiority",
            "Histogram distance is not state fidelity or tomography",
        ],
        "gate_diagnostics": diagnostics,
        "tooling": tooling,
        "read_only": True,
    }
    return dict(redact(result))
