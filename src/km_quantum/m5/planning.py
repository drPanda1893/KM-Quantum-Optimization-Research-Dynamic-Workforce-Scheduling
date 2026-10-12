"""Freeze paired PUB ordering and the unsigned three-job M5 execution proposal."""

from __future__ import annotations

import copy
import math
import random
import re
from typing import Any

from km_quantum.m5.approvals import plan_hash, validate_plan
from km_quantum.m5.mapping import MeasurementMapping
from km_quantum.m5.models import M5Config
from km_quantum.m5.provider import SafetyError, safe_runtime_options, validate_preflight

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HARDWARE_SOURCES = {"provider", "ibm_hardware"}
_OFFLINE_SOURCES = {"offline_synthetic", "offline_test_backend", "mock"}
_WIDTHS = {"A_single_job": 2, "E_overlap": 4, "H_workload": 5, "K_bounded_slack": 7}


def _mapping(record: dict[str, Any], config: M5Config) -> tuple[int, dict[str, Any]]:
    value = record.get("mapping", {})
    try:
        mapping = MeasurementMapping(
            **{
                key: tuple(value[key])
                for key in (
                    "m2_to_logical",
                    "logical_to_initial_physical",
                    "m2_to_final_physical",
                    "m2_to_classical",
                    "active_physical_qubits",
                )
            },
            provider_width=value["provider_width"],
            register_name=value.get("register_name", "m2"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise SafetyError(f"Prepared circuit mapping is invalid: {error}") from None
    n = len(mapping.m2_to_logical)
    if mapping.register_name != "m2" or not 1 <= n <= config.max_active_qubits:
        raise SafetyError("Every circuit must measure a bounded named m2 register")
    if len(mapping.active_physical_qubits) > config.max_active_qubits:
        raise SafetyError("Prepared active simulation width exceeds the guard")
    if record.get("num_bits", n) != n:
        raise SafetyError("Prepared record width differs from its actual m2 mapping")
    return n, copy.deepcopy(value)


def _runtime_settings(evidence: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    description: dict[str, Any] = {
        "init_qubits": True,
        "initialization_definition": "Normal provider reset before each shot remains enabled",
        "rep_delay": None,
        "rep_delay_source": "Backend default; no concrete provider configuration in offline draft",
        "optional_processing": "DD, gate twirling and measurement twirling explicitly disabled",
    }
    delay: float | None = None
    if evidence is not None:
        info = evidence.get("rep_delay", {})
        supported = info.get("supported")
        value = info.get("value")
        description["rep_delay_source"] = info.get("source", "Provider default not reported")
        if supported is True and value is not None:
            valid_number = (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                and value >= 0
            )
            limits = info.get("range")
            if (
                not valid_number
                or not info.get("source")
                or not isinstance(limits, (tuple, list))
                or len(limits) != 2
                or not all(
                    isinstance(item, (int, float))
                    and not isinstance(item, bool)
                    and math.isfinite(item)
                    for item in limits
                )
                or not limits[0] <= value <= limits[1]
            ):
                raise SafetyError("Documented supported rep-delay value and range are required")
            delay = float(value)
            description.update(rep_delay=delay, rep_delay_range=list(limits))
        else:
            description["rep_delay"] = value
            description["rep_delay_requested"] = False
    return safe_runtime_options(rep_delay=delay), description


def make_plan(
    prepared: dict[str, Any],
    config: M5Config,
    provider_evidence: dict[str, Any] | None = None,
    *,
    usage_model: dict[str, Any] | None = None,
    provider_limit_assessment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic unapproved plan without accessing any provider.

    Each pair remains adjacent. The second block reverses the complete first
    block, including pair orientation. This is a planned bias reduction policy;
    the provider's actual within-job schedule is not claimed to follow it.
    """
    required_hashes = (
        "content_sha256",
        "selection_manifest_sha256",
        "source_context_sha256",
        "target_snapshot_sha256",
    )
    if any(not _SHA256.fullmatch(str(prepared.get(key, ""))) for key in required_hashes):
        raise SafetyError(
            "All frozen source, selection, compilation and target hashes are required"
        )
    source = prepared.get("source_kind")
    if source not in _HARDWARE_SOURCES | _OFFLINE_SOURCES:
        raise SafetyError("Prepared artifact has an unknown backend provenance")
    real = source in _HARDWARE_SOURCES
    if real:
        if provider_evidence is None:
            raise SafetyError(
                "Real hardware preparation requires explicit verified provider evidence"
            )
        validate_preflight(provider_evidence)
        if provider_evidence["backend"] != prepared.get("backend_name"):
            raise SafetyError("Provider backend differs from the prepared hardware target")
    elif provider_evidence is not None:
        raise SafetyError(
            "Synthetic compilation cannot become a hardware plan through provider evidence"
        )
    subset = prepared.get("physical_subset", [])
    if (
        not isinstance(subset, (tuple, list))
        or len(subset) != 7
        or len(set(subset)) != 7
        or any(type(item) is not int or item < 0 for item in subset)
    ):
        raise SafetyError("The prepared layout requires seven fixed distinct physical qubits")
    records = prepared.get("records", [])
    if len(records) != 28 or len({record.get("circuit_id") for record in records}) != 28:
        raise SafetyError("Exactly twenty-four main and four distinct smoke circuits are required")
    main = [record for record in records if record.get("kind") == "main"]
    smoke = sorted(
        [record for record in records if record.get("kind") == "smoke"],
        key=lambda record: record["circuit_id"],
    )
    if len(main) != 24 or len(smoke) != 4:
        raise SafetyError("The fixed study requires 24 main circuits and four smoke controls")
    indexed: dict[tuple[str, str, Any], dict[str, Any]] = {}
    circuits: dict[str, Any] = {}
    for record in sorted(records, key=lambda item: item["circuit_id"]):
        identity = record.get("circuit_id")
        if not isinstance(identity, str) or not identity:
            raise SafetyError("Every prepared circuit requires a persistent string identity")
        n, mapping = _mapping(record, config)
        qpy = record.get("qpy", {}).get("provider", {})
        if not qpy.get("path") or not _SHA256.fullmatch(str(qpy.get("sha256", ""))):
            raise SafetyError(
                "Every provider circuit requires its frozen QPY path and raw-byte hash"
            )
        circuits[identity] = {
            "qpy_sha256": qpy["sha256"],
            "path": qpy["path"],
            "num_bits": n,
            "mapping": mapping,
        }
        if record["kind"] == "main":
            case = record.get("reference_name")
            if case not in config.references or n != _WIDTHS[case]:
                raise SafetyError(
                    "Prepared main circuit differs from the fixed case or logical width"
                )
            key = (case, record.get("algorithm"), record.get("p"))
            if key in indexed:
                raise SafetyError("Duplicate case/algorithm/depth scientific identity")
            indexed[key] = record
        elif n != 7:
            raise SafetyError("Smoke controls require the relevant seven-bit m2 register")
    groups = []
    for case in config.references:
        for depth in config.depths:
            try:
                groups.append(
                    [
                        indexed.pop((case, algorithm, depth))
                        for algorithm in ("standard", "warm_start")
                    ]
                )
            except KeyError:
                raise SafetyError("Missing fixed Standard/Warm QAOA pair") from None
        try:
            groups.append(
                [indexed.pop((case, algorithm, None)) for algorithm in ("uniform", "warm_product")]
            )
        except KeyError:
            raise SafetyError("Missing frozen uniform/warm-product control pair") from None
    if indexed:
        raise SafetyError("Unexpected algorithm/depth identity in prepared study")
    for group in groups:
        initial = group[0]["mapping"]["logical_to_initial_physical"]
        if group[1]["mapping"]["logical_to_initial_physical"] != initial:
            raise SafetyError("Standard/Warm pair must use the same frozen initial layout")
    random.Random(config.pub_order_seed).shuffle(groups)
    first_block = [record for group in groups for record in group]
    runtime, settings = _runtime_settings(provider_evidence)

    def pub(record: dict[str, Any], block: int) -> dict[str, Any]:
        identity = record["circuit_id"]
        return {
            "pub_id": f"block-{block}-{identity}",
            "circuit_id": identity,
            "circuit_hash": circuits[identity]["qpy_sha256"],
            "num_bits": circuits[identity]["num_bits"],
            "shots": config.smoke_shots if block == 0 else config.main_shots,
            "case_name": record.get("reference_name"),
            "block": block,
        }

    jobs = [
        {
            "id": "smoke",
            "kind": "smoke",
            "max_execution_time": 40,
            "pubs": [pub(record, 0) for record in smoke],
        },
        {
            "id": "main-block-1",
            "kind": "main",
            "max_execution_time": 40,
            "pubs": [pub(record, 1) for record in first_block],
        },
        {
            "id": "main-block-2",
            "kind": "main",
            "max_execution_time": 40,
            "pubs": [pub(record, 2) for record in reversed(first_block)],
        },
    ]
    if real:
        assert provider_evidence is not None
        for job in jobs:
            estimates = (
                usage_model.get("execution_estimates", {})
                if usage_model is not None
                else provider_evidence.get("execution_estimates", {})
            )
            estimate = estimates.get(job["id"], {})
            job.update(
                estimated_qpu_seconds=estimate.get("seconds"),
                estimate_source=estimate.get("source"),
            )
    result = {
        "schema_version": 1,
        "artifact_type": "m5_hardware_execution_plan" if real else "m5_offline_execution_draft",
        "status": "READY_FOR_APPROVAL" if real else "OFFLINE_READY",
        "provider_verified": real,
        "source_kind": "ibm_hardware" if real else source,
        "compilation_source_kind": source,
        "backend": prepared["backend_name"] if real else None,
        "offline_backend_fixture": prepared["backend_name"] if not real else None,
        "instance_binding": provider_evidence["instance_binding"]
        if real and provider_evidence
        else None,
        "layout": list(subset),
        "prepared_manifest_sha256": prepared["content_sha256"],
        "selection_manifest_sha256": prepared["selection_manifest_sha256"],
        "source_context_sha256": prepared["source_context_sha256"],
        "target_snapshot_sha256": prepared["target_snapshot_sha256"],
        "circuits": circuits,
        "jobs": jobs,
        "budget": {
            "max_provider_jobs": config.max_provider_jobs,
            "max_total_shots": config.max_total_shots,
            "max_qpu_seconds_per_job": config.max_qpu_seconds_per_job,
            "max_total_qpu_seconds": config.max_total_qpu_seconds,
            "max_cost_eur": config.max_cost_eur,
        },
        "runtime_options": runtime,
        "runtime_settings_description": settings,
        "pub_order_policy": {
            "seed": config.pub_order_seed,
            "groups": "Standard/Warm pair per case/depth and uniform/warm-product pair per case",
            "block_1": "Shuffle twelve complete pairs with Python Random(seed)",
            "block_2": "Reverse the complete ordered block, including each pair orientation",
            "actual_provider_order_guaranteed": False,
        },
        "hardware_enabled": False,
        "submission_approved": False,
        "hardware_used": False,
    }
    if real:
        # Numerical models and concrete QPY service-limit assessments are part
        # of the unsigned plan hash. They cannot be replaced by legacy limits
        # or an unbound manual seconds value.
        if usage_model is not None:
            result["local_usage_model"] = copy.deepcopy(usage_model)
            result["usage_model_sha256"] = usage_model.get("model_sha256")
        if provider_limit_assessment is not None:
            result["provider_limit_assessment"] = copy.deepcopy(provider_limit_assessment)
            result["provider_limit_assessment_sha256"] = provider_limit_assessment.get(
                "content_sha256"
            )
    # Validate study structure before potentially missing concrete contracts
    # are converted into an explicitly unapprovable diagnostic draft.
    validate_plan(result, require_hardware=False)
    if real:
        assert provider_evidence is not None
        contract_evidence = copy.deepcopy(provider_evidence)
        if usage_model is not None:
            # These are explicitly local calculations, not new provider facts.
            # Live binding/usage timestamps and service observations stay intact.
            contract_evidence["local_usage_model"] = copy.deepcopy(usage_model)
            contract_evidence["execution_estimates"] = copy.deepcopy(
                usage_model.get("execution_estimates", {})
            )
        try:
            validate_preflight(contract_evidence, result)
        except SafetyError as error:
            # Preflight must first learn the concrete PUB order before external
            # usage estimates can be bound. This draft is explicitly unapprovable.
            result.update(
                artifact_type="m5_hardware_execution_draft",
                status="BLOCKED",
                provider_verified=False,
                readiness_diagnostic=str(error),
                diagnostic_kind=getattr(error, "diagnostic_kind", "PROVIDER_CONTRACT_ERROR"),
            )
            validate_plan(result, require_hardware=False)
        else:
            validate_plan(result)
    result["plan_hash"] = plan_hash(result)
    return result
