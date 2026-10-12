"""Explicit offline commands and separately invoked, fail-closed IBM boundaries."""

from __future__ import annotations

import argparse
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from km_quantum.m4.io import canonical_hash, read_json
from km_quantum.m5.analysis import analyze_results
from km_quantum.m5.approvals import approval_request, plan_hash, validate_approval, validate_plan
from km_quantum.m5.io import atomic_json, output_directory, read_sealed, sealed_json
from km_quantum.m5.ledger import JobLedger
from km_quantum.m5.models import M5Config
from km_quantum.m5.provider import (
    IBMRuntimeAdapter,
    LocalProviderConfig,
    SafetyError,
    preflight,
    redact,
)
from km_quantum.m5.snapshots import load_qpy, snapshot_backend
from km_quantum.m5.state import transition
from km_quantum.m5.workflow import (
    artifact_path,
    load_prepared,
    prepare,
    verify_local,
    verify_plan_inventory_binding,
)
from km_quantum.qaoa.references import load_references

COMMANDS = (
    "prepare",
    "verify-local",
    "provider-preflight",
    "plan",
    "submit-next",
    "collect",
    "analyze",
    "acceptance",
)


def add_m5_parser(commands: Any) -> None:
    parser = commands.add_parser(
        "m5", help="Frozen M4 circuit hardware validation; offline by default"
    )
    sub = parser.add_subparsers(dest="m5_command", required=True)
    for name in COMMANDS:
        command = sub.add_parser(name)
        command.add_argument("--root", default=".")
        command.add_argument("--output", default="results/m5")
        if name == "prepare":
            command.add_argument("--config", default="configs/m5_baseline.json")
        if name in {"provider-preflight", "submit-next", "collect"}:
            command.add_argument("--provider-config")
        if name == "provider-preflight":
            command.add_argument(
                "--usage-estimates",
                help=(
                    "Optional external estimates for audit; "
                    "the bound local model controls readiness"
                ),
            )
        if name == "submit-next":
            command.add_argument("--approval", required=True)
        if name == "acceptance":
            command.add_argument("--tooling", default="results/m5/preflight/tooling.json")


def _config(args: argparse.Namespace) -> LocalProviderConfig:
    config = (
        LocalProviderConfig.from_file(args.provider_config)
        if args.provider_config
        else LocalProviderConfig.from_environment()
    )
    args._m5_redaction_secrets = (config.instance, config.token or "")
    return config


def select_physical_subset(target: Any) -> tuple[int, ...]:
    """Smallest-ID connected BFS prefix; no observed-result-dependent selection."""
    edges: dict[int, set[int]] = {}
    for name in target.operation_names:
        for qargs, properties in target[name].items():
            if (
                qargs is None
                or len(qargs) != 2
                or properties is None
                or properties.error is None
                or properties.duration is None
            ):
                continue
            if (
                any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value < 0
                    for value in (properties.error, properties.duration)
                )
                or properties.error > 1
            ):
                continue
            left, right = qargs
            edges.setdefault(left, set()).add(right)
            edges.setdefault(right, set()).add(left)
    for start in sorted(edges):
        queue: list[int] = [start]
        visited: list[int] = []
        while queue and len(visited) < 7:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.append(node)
            queue.extend(sorted(edges.get(node, set()) - set(visited)))
        if len(visited) == 7:
            return tuple(visited)
    raise SafetyError("No calibrated connected seven-qubit subset is available")


def _make_plan(root: Path, output: Path) -> dict[str, Any]:
    from km_quantum.m5.planning import make_plan

    prepared = load_prepared(root, output)
    verify_local(root, output)
    if any(row["device_noise_status"] == "BLOCKED" for row in prepared["records"]):
        raise SafetyError("Missing device calibration blocks the hardware comparison plan")
    if prepared["source_kind"] == "provider":
        snapshot = read_sealed(output / "target_snapshot.json")
        if not snapshot.get("calibration_timestamp"):
            raise SafetyError("Concrete calibration timestamp is required for a hardware plan")
    evidence_path = output / (
        "provider_account_preflight.json"
        if prepared["source_kind"] == "provider"
        and (output / "provider_account_preflight.json").exists()
        else "provider_preflight.json"
    )
    evidence = read_json(evidence_path) if evidence_path.exists() else None
    usage_path = output / "local_usage_model.json"
    assessment_path = output / "provider_limit_assessment.json"
    if (
        prepared["source_kind"] == "provider"
        and (output / "provider_execution_draft.json").exists()
    ):
        plan = read_json(output / "provider_execution_draft.json")
        # This freezes the exact already assessed artifact. Provider freshness
        # is checked during preflight; opening a blocked draft cannot approve it.
        validate_plan(plan, require_hardware=plan.get("provider_verified") is True)
        verify_plan_inventory_binding(plan, prepared)
        if plan.get("provider_verified") is True:
            final_evidence = read_json(output / "provider_preflight.json")
            if final_evidence.get("status") != "PROVIDER_PREFLIGHT_READY":
                raise SafetyError(
                    "Latest hardware preflight is blocked; the cached draft cannot report READY",
                    diagnostic_kind=final_evidence.get(
                        "diagnostic_kind", "PROVIDER_CONTRACT_ERROR"
                    ),
                )
            from km_quantum.m5.provider import validate_preflight

            validate_preflight(final_evidence, plan)
    else:
        plan = make_plan(
            prepared,
            M5Config(),
            provider_evidence=evidence,
            usage_model=read_json(usage_path) if usage_path.exists() else None,
            provider_limit_assessment=read_json(assessment_path)
            if assessment_path.exists()
            else None,
        )
    atomic_json(output / "execution_plan.json", plan, immutable=True)
    request_path = output / "approval_request.json"
    if request_path.exists():
        request = read_json(request_path)
        if request["plan_hash"] != plan_hash(plan) or request["approved"] is not False:
            raise SafetyError("Existing approval proposal differs or is no longer unsigned")
    else:
        expiry = (datetime.now(UTC) + timedelta(hours=24)).isoformat()
        atomic_json(request_path, approval_request(plan, expiry), immutable=True)
    state_path = output / "state.json"
    current = read_json(state_path)["state"] if state_path.exists() else "DRAFT"
    if current in {"DRAFT", "OFFLINE_READY"}:
        transition(
            state_path,
            "OFFLINE_READY",
            reason="Local compilation verified",
            plan_hash=plan_hash(plan),
        )
    if plan["provider_verified"] and current in {"DRAFT", "OFFLINE_READY"}:
        transition(
            output / "state.json",
            "PROVIDER_PREFLIGHT_READY",
            reason="Read-only verified provider",
            plan_hash=plan_hash(plan),
        )
        transition(
            output / "state.json",
            "READY_FOR_APPROVAL",
            reason="Unsigned concrete hardware plan",
            plan_hash=plan_hash(plan),
        )
    elif plan["provider_verified"] and current == "PROVIDER_PREFLIGHT_READY":
        transition(
            state_path,
            "READY_FOR_APPROVAL",
            reason="Resume unsigned concrete plan",
            plan_hash=plan_hash(plan),
        )
    status = plan["status"]
    if current in {
        "APPROVED",
        "SUBMITTED",
        "COLLECTED",
        "ANALYZED",
        "SUBMISSION_UNKNOWN",
        "PARTIAL",
        "FAILED",
        "CANCELLED",
    }:
        status = current
    return {
        "status": status,
        "plan_hash": plan_hash(plan),
        "hardware_used": False,
        "provider_configuration_status": "VERIFIED" if plan["provider_verified"] else "BLOCKED",
        "diagnostic_kind": plan.get("diagnostic_kind"),
        "readiness_diagnostic": plan.get("readiness_diagnostic"),
        "planned_jobs": 3,
        "planned_pubs": 52,
        "planned_shots": 49664,
        "submission_approved": False,
    }


def _provider_preflight(args: argparse.Namespace, root: Path, output: Path) -> dict[str, Any]:
    if (output / "prepared_manifest.json").exists() and load_prepared(root, output)[
        "source_kind"
    ] != "provider":
        raise SafetyError("Use a new results/m5-provider output for actual provider compilation")
    config = _config(args)
    adapter = IBMRuntimeAdapter(config)
    evidence = preflight(config, adapter)
    args._m5_redaction_secrets = adapter._secret_values()
    # The optional old input remains recorded as externally supplied information;
    # bare seconds cannot override the automatically bound numerical model.
    if args.usage_estimates:
        evidence["external_estimates_not_used_for_approval"] = redact(
            read_json(args.usage_estimates), secrets=args._m5_redaction_secrets
        )
    atomic_json(output / "provider_preflight.json", evidence)
    if evidence["status"] != "PROVIDER_PREFLIGHT_READY":
        return evidence
    backend = adapter.backend_for_snapshot()
    subset = select_physical_subset(backend.target)
    try:
        local_result = prepare(
            root,
            output,
            backend=backend,
            physical_subset=subset,
            calibration_timestamp=evidence.get("calibration_timestamp"),
        )
    except Exception as error:
        evidence.update(
            status="BLOCKED",
            diagnostic_kind="LOCAL_CHANNEL_OR_SIMULATION_ERROR",
            local_preparation_status="BLOCKED",
            diagnostic=redact(str(error), secrets=adapter._secret_values()),
        )
        atomic_json(output / "provider_preflight.json", evidence)
        return evidence
    if local_result["status"] == "BLOCKED" or not evidence.get("calibration_timestamp"):
        evidence.update(
            status="BLOCKED",
            local_preparation_status=local_result["status"],
            readiness_diagnostic="Required device calibration or timestamp is missing",
            diagnostic_kind="MISSING_REAL_CALIBRATION",
        )
        atomic_json(output / "provider_preflight.json", evidence)
        return evidence
    # Preparation may take longer than the five-minute live-account window.
    # Read again; historical target retrieval/calibration timestamps stay intact.
    evidence = preflight(config, adapter)
    atomic_json(output / "provider_account_preflight.json", evidence)
    if evidence["status"] != "PROVIDER_PREFLIGHT_READY":
        evidence["local_preparation_status"] = local_result["status"]
        atomic_json(output / "provider_preflight.json", evidence)
        return evidence
    from km_quantum.m5.planning import make_plan
    from km_quantum.m5.provider_limits import assess_plan_limits
    from km_quantum.m5.usage import estimate_plan_usage

    prepared = load_prepared(root, output)
    snapshot = read_sealed(output / "target_snapshot.json")
    current_snapshot = snapshot_backend(backend, subset)
    if any(
        current_snapshot[key] != snapshot[key]
        for key in ("instructions", "qubits", "physical_subset", "backend_name", "backend_width")
    ):
        raise SafetyError(
            "Actual Target differs from the frozen calibration snapshot; use a new output",
            diagnostic_kind="MISSING_REAL_CALIBRATION",
        )
    draft = make_plan(prepared, M5Config(), provider_evidence=evidence)
    circuits = {
        identity: load_qpy(
            artifact_path(root, record["path"], output),
            expected_sha256=record["qpy_sha256"],
        )
        for identity, record in draft["circuits"].items()
    }
    hashes = {identity: record["qpy_sha256"] for identity, record in draft["circuits"].items()}
    assessment = assess_plan_limits(draft, circuits, qpy_hashes=hashes)
    usage_model = estimate_plan_usage(
        draft,
        circuits,
        backend.target,
        rep_delay=evidence.get("rep_delay"),
        target_snapshot_sha256=prepared["target_snapshot_sha256"],
        qpy_hashes=hashes,
    )
    atomic_json(output / "provider_limit_assessment.json", assessment, immutable=True)
    atomic_json(output / "local_usage_model.json", usage_model, immutable=True)
    plan = make_plan(
        prepared,
        M5Config(),
        provider_evidence=evidence,
        usage_model=usage_model,
        provider_limit_assessment=assessment,
    )
    atomic_json(output / "provider_execution_draft.json", plan, immutable=True)
    evidence = preflight(config, adapter, plan=plan)
    evidence["local_preparation_status"] = local_result["status"]
    evidence["plan_hash"] = plan_hash(plan)
    evidence["usage_model_sha256"] = usage_model["model_sha256"]
    evidence["provider_limit_assessment_sha256"] = assessment["content_sha256"]
    atomic_json(output / "provider_preflight.json", evidence)
    return evidence


def _submit(args: argparse.Namespace, root: Path, output: Path) -> dict[str, Any]:
    from km_quantum.m5.approvals import validate_human_approval

    plan = read_json(output / "execution_plan.json")
    validate_plan(plan)
    approval = read_json(args.approval)
    if (
        approval.get("approved") is not True
        or approval.get("signed_by") != "Florian"
        or approval.get("plan_hash") != plan_hash(plan)
    ):
        raise SafetyError("Exact plan requires human approval; no provider connection was made")
    validate_human_approval(plan, approval)
    verify_local(root, output)
    verify_plan_inventory_binding(plan, load_prepared(root, output))
    state_path = output / "state.json"
    state = read_json(state_path)
    if state.get("state") not in {"READY_FOR_APPROVAL", "APPROVED", "COLLECTED"}:
        raise SafetyError("Local state does not allow a next submission")
    if state.get("plan_hash") != plan_hash(plan):
        raise SafetyError("Local state belongs to a different execution plan")
    ledger = JobLedger(output / "jobs/ledger.json")
    ledger_data = ledger.snapshot()
    JobLedger._check_binding(ledger_data, plan)
    jobs = ledger_data["jobs"]
    if len(jobs) >= 3 or (jobs and jobs[-1]["status"] != "COLLECTED"):
        raise SafetyError("Prior job is unresolved or the complete budget was already reserved")
    config = _config(args)
    adapter = IBMRuntimeAdapter(config)
    evidence = preflight(config, adapter, plan=plan)
    atomic_json(output / "provider_preflight.json", evidence)
    circuits = {
        key: artifact_path(root, row["path"], output) for key, row in plan["circuits"].items()
    }
    validate_approval(plan, approval, evidence)
    current = read_json(state_path)["state"] if state_path.exists() else "DRAFT"
    if current == "READY_FOR_APPROVAL":
        transition(
            state_path,
            "APPROVED",
            reason="Exact human approval validated",
            plan_hash=plan_hash(plan),
        )
    try:
        record = ledger.submit_next(plan, approval, evidence, adapter, circuits)
    except Exception:
        jobs = ledger.snapshot()["jobs"]
        if jobs and jobs[-1]["status"] == "SUBMISSION_UNKNOWN":
            transition(
                state_path,
                "SUBMISSION_UNKNOWN",
                reason="Ambiguous provider response; reservation retained",
                plan_hash=plan_hash(plan),
            )
        raise
    transition(
        state_path,
        record["status"],
        reason="One provider job reserved and submitted",
        plan_hash=plan_hash(plan),
    )
    return record


def _collect(args: argparse.Namespace, root: Path, output: Path) -> dict[str, Any]:
    plan = read_json(output / "execution_plan.json")
    validate_plan(plan, require_hardware=False)
    verify_plan_inventory_binding(plan, load_prepared(root, output))
    ledger = JobLedger(output / "jobs/ledger.json")
    if not ledger.snapshot()["jobs"]:
        result = {
            "status": "NOT_SUBMITTED",
            "hardware_used": False,
            "raw_results": [],
            "missing_pubs": [pub["pub_id"] for job in plan["jobs"] for pub in job["pubs"]],
            "job_records": [],
            "reserved_qpu_seconds": 0,
        }
    else:
        validate_plan(plan)
        verify_local(root, output)
        result = ledger.collect(plan, IBMRuntimeAdapter(_config(args)))
    result["plan_hash"] = plan_hash(plan)
    atomic_json(output / "raw/collected.json", result)
    if result["status"] in {"COLLECTED", "PARTIAL", "FAILED", "CANCELLED", "SUBMISSION_UNKNOWN"}:
        transition(
            output / "state.json",
            result["status"],
            reason="Read-only bounded collection",
            plan_hash=plan_hash(plan),
        )
    return result


def _analyze(root: Path, output: Path) -> dict[str, Any]:
    prepared = load_prepared(root, output)
    plan = read_json(output / "execution_plan.json")
    validate_plan(plan, require_hardware=False)
    verify_plan_inventory_binding(plan, prepared)
    raw_path = output / "raw/collected.json"
    collected: dict[str, Any] = (
        read_json(raw_path)
        if raw_path.exists()
        else {"raw_results": [], "hardware_used": False, "plan_hash": plan_hash(plan)}
    )
    if collected["plan_hash"] != plan_hash(plan):
        raise SafetyError("Raw data belong to a different plan")
    known_ids = {pub["pub_id"] for job in plan["jobs"] for pub in job["pubs"]}
    actual_ids = [row["pub_id"] for row in collected["raw_results"]]
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) - known_ids:
        raise SafetyError("Raw results contain duplicate or unknown persistent PUB IDs")
    records = {row["circuit_id"]: row for row in prepared["records"]}
    expected = [
        {
            **pub,
            "circuit_sha256": pub["circuit_hash"],
            "m2_to_classical": records[pub["circuit_id"]]["mapping"]["m2_to_classical"],
        }
        for job in plan["jobs"]
        if job["kind"] == "main"
        for pub in job["pubs"]
    ]
    predictions = {}
    for pub in expected:
        circuit_id = pub["circuit_id"]
        prediction = read_sealed(output / "predictions" / f"{circuit_id}.json")
        if prediction["content_sha256"] != records[circuit_id]["prediction_sha256"]:
            raise SafetyError("Analysis prediction differs from the precommitted record")
        predictions[circuit_id] = {
            "logical_ideal": prediction["logical_ideal"],
            "compiled_ideal": prediction["compiled_ideal"],
        }
        noise = prediction["device_noise"]
        if noise["status"] != "BLOCKED":
            predictions[circuit_id]["device_after_readout"] = noise["probabilities_after_readout"]
    ids = {pub["pub_id"] for pub in expected}
    result = analyze_results(
        expected,
        [row for row in collected["raw_results"] if row["pub_id"] in ids],
        predictions,
        load_references(root),
        mc_seed=M5Config().monte_carlo_seed,
    )
    result.update(
        plan_hash=plan_hash(plan),
        collected_content_sha256=canonical_hash(collected),
        prepared_manifest_sha256=prepared["content_sha256"],
    )
    sealed_json(output / "summaries/analysis.json", result, immutable=False)
    if result["analysis_status"] == "ANALYZED":
        transition(
            output / "state.json",
            "ANALYZED",
            reason="All real main PUBs analyzed",
            plan_hash=plan_hash(plan),
        )
    return result


def execute_m5(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    root = Path(args.root).resolve()
    output = output_directory(root, args.output)
    try:
        if args.m5_command == "prepare":
            path = Path(args.config)
            config = M5Config.model_validate(read_json(path if path.is_absolute() else root / path))
            result = prepare(root, output, config)
            transition(
                output / "state.json",
                "BLOCKED" if result["status"] == "BLOCKED" else "OFFLINE_READY",
                reason="Offline preparation and equivalence passed",
            )
        elif args.m5_command == "verify-local":
            result = verify_local(root, output)
        elif args.m5_command == "provider-preflight":
            result = _provider_preflight(args, root, output)
        elif args.m5_command == "plan":
            result = _make_plan(root, output)
        elif args.m5_command == "submit-next":
            result = _submit(args, root, output)
        elif args.m5_command == "collect":
            result = _collect(args, root, output)
        elif args.m5_command == "analyze":
            result = _analyze(root, output)
        else:
            from km_quantum.m5.readiness import build_acceptance

            tooling_path = Path(args.tooling)
            tooling_path = tooling_path if tooling_path.is_absolute() else root / tooling_path
            tooling = read_json(tooling_path) if tooling_path.exists() else None
            result = build_acceptance(root, output, tooling)
            atomic_json(output / "m5-acceptance.json", result)
        return result, 0 if result.get("status") not in {
            "BLOCKED",
            "FAILED",
            "OWNER_CONFIGURATION_REQUIRED",
        } else 1
    except Exception as exc:
        # No raw provider exception can escape into the top-level CLI logger.
        secrets = getattr(args, "_m5_redaction_secrets", ())
        result = {
            "status": "OWNER_CONFIGURATION_REQUIRED"
            if args.m5_command == "provider-preflight"
            and getattr(exc, "diagnostic_kind", "") == "OWNER_CONFIGURATION_REQUIRED"
            else "BLOCKED",
            "diagnostic_kind": getattr(exc, "diagnostic_kind", "LOCAL_IMPLEMENTATION_ERROR"),
            "hardware_used": False,
            "error": redact(str(exc), secrets=secrets),
        }
        return result, 1
