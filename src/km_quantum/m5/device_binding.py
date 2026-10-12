"""Pure binding of fresh device observations to an immutable scientific basis.

A calibration timestamp can advance while the observed parameters stay equal.
That is recorded separately and never rewrites the frozen snapshot or plan.
Unknown unused Target fields stay unknown; required native/noise/timing facts
cannot be guessed. No SDK, account, network or submission is constructed here.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping
from typing import Any

from km_quantum.m4.io import canonical_hash
from km_quantum.m5.provider import (
    MAX_PREFLIGHT_AGE_SECONDS,
    SafetyError,
    parse_time,
    redact,
    utc_now,
)

_TIMING_KEYS = {"granularity", "min_length", "pulse_alignment", "acquire_alignment"}
_SHA = re.compile(r"^[0-9a-f]{64}$")
DIAGNOSTIC_KIND = "LIVE_DEVICE_BINDING_ERROR"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SafetyError(message, diagnostic_kind=DIAGNOSTIC_KIND)


def _number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _seal(snapshot: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in snapshot.items() if key != "content_sha256"}
    try:
        digest = canonical_hash(payload)
    except (TypeError, ValueError):
        raise SafetyError(
            "Device snapshot is not finite public JSON", diagnostic_kind=DIAGNOSTIC_KIND
        ) from None
    _require(
        snapshot.get("content_sha256") == digest,
        "Device snapshot content seal changed or is missing",
    )
    return digest


def snapshot_with_calibration_timestamp(
    snapshot: Mapping[str, Any], calibration_timestamp: str
) -> dict[str, Any]:
    """Bridge the SDK properties timestamp explicitly, preserving retrieval time."""
    _require(isinstance(snapshot, Mapping), "Device snapshot must be a public mapping")
    try:
        parsed = parse_time(calibration_timestamp)
    except SafetyError:
        raise SafetyError(
            "SDK calibration timestamp must include a valid timezone",
            diagnostic_kind=DIAGNOSTIC_KIND,
        ) from None
    if "content_sha256" in snapshot:
        _seal(snapshot)
    result = copy.deepcopy(
        {key: value for key, value in snapshot.items() if key != "content_sha256"}
    )
    existing = result.get("calibration_timestamp")
    _require(
        existing is None or parse_time(existing) == parsed,
        "Snapshot and SDK calibration timestamps disagree",
    )
    result["calibration_timestamp"] = calibration_timestamp
    result["missing"] = [key for key in result.get("missing", []) if key != "calibration_timestamp"]
    result["timestamp_definition"] = (
        "Provider backend.properties().last_update_date; retrieved_at is separate"
    )
    return {**result, "content_sha256": canonical_hash(result)}


def _facts(
    snapshot: Mapping[str, Any], required_operations: set[str]
) -> tuple[dict[str, Any], list[str]]:
    _require(
        snapshot.get("artifact_type") == "m5_backend_snapshot"
        and snapshot.get("source") == "provider",
        "A provider device snapshot is required",
    )
    _require(
        snapshot.get("hardware_used") is False and snapshot.get("credentials_exported") is False,
        "Device snapshots must be read-only public observations",
    )
    width, subset = snapshot.get("backend_width"), snapshot.get("physical_subset")
    _require(
        type(width) is int and width > 0 and isinstance(subset, list) and 0 < len(subset) <= 10,
        "Known device width and physical subset are required",
    )
    assert isinstance(width, int) and isinstance(subset, list)
    _require(
        all(type(qubit) is int and 0 <= qubit < width for qubit in subset)
        and len(set(subset)) == len(subset),
        "Physical subset is invalid",
    )
    operations = snapshot.get("operation_names")
    _require(
        isinstance(operations, list)
        and bool(operations)
        and all(isinstance(name, str) and name for name in operations)
        and len(set(operations)) == len(operations),
        "Known distinct Target operations are required",
    )
    assert isinstance(operations, list)
    timing = snapshot.get("target_timing")
    _require(
        isinstance(timing, Mapping),
        "Target dt and timing constraints were not frozen; a new plan is required",
    )
    assert isinstance(timing, Mapping)
    _require(
        type(snapshot.get("schema_version")) is int and snapshot["schema_version"] == 2,
        "Snapshot must use the current versioned Target timing contract",
    )
    _require(
        _number(timing.get("dt_seconds")) and timing["dt_seconds"] > 0,
        "Known positive Target dt in seconds is required",
    )
    constraints = timing.get("timing_constraints")
    _require(
        isinstance(constraints, Mapping)
        and set(constraints) == _TIMING_KEYS
        and all(type(value) is int and value > 0 for value in constraints.values()),
        "All four Target timing constraints must be known positive integers",
    )
    _require(
        timing.get("source") == "qiskit.transpiler.Target.dt/timing_constraints()",
        "Target timing provenance is missing",
    )
    instructions = snapshot.get("instructions")
    _require(
        isinstance(instructions, list) and bool(instructions),
        "Native instruction facts are missing",
    )
    assert isinstance(instructions, list)
    normalized_instructions: dict[tuple[str, tuple[int, ...]], Any] = {}
    unknown: list[str] = []
    for row in instructions:
        _require(isinstance(row, Mapping), "Native instruction record is malformed")
        name, qubits = row.get("name"), row.get("qubits")
        _require(
            isinstance(name, str)
            and name in operations
            and isinstance(qubits, list)
            and bool(qubits)
            and all(type(qubit) is int and qubit in subset for qubit in qubits)
            and len(set(qubits)) == len(qubits),
            "Native instruction identity is malformed",
        )
        identity = (name, tuple(qubits))
        _require(identity not in normalized_instructions, "Duplicate native instruction facts")
        for field in ("duration_seconds", "error"):
            value = row.get(field)
            if value is None:
                _require(
                    field in row.get("missing", []), "Unknown instruction fact was not declared"
                )
                unknown.append(f"instruction:{name}:{qubits}:{field}")
            else:
                _require(
                    _number(value) and (field != "error" or value <= 1),
                    "Native instruction numeric fact is invalid",
                )
        if name in required_operations:
            _require(_number(row.get("duration_seconds")), "Required native duration is unknown")
        normalized_instructions[identity] = copy.deepcopy(dict(row))
    _require(
        required_operations <= {name for name, _ in normalized_instructions},
        "Required native operation is absent from the Target",
    )
    qubit_rows = snapshot.get("qubits")
    _require(isinstance(qubit_rows, list), "Qubit calibration facts are missing")
    assert isinstance(qubit_rows, list)
    normalized_qubits = {}
    for row in qubit_rows:
        _require(
            isinstance(row, Mapping)
            and type(row.get("physical_id")) is int
            and row["physical_id"] in subset,
            "Qubit calibration identity is malformed",
        )
        identity = row["physical_id"]
        _require(identity not in normalized_qubits, "Duplicate qubit calibration facts")
        for field in ("t1_seconds", "t2_seconds", "frequency_hz", "readout_error"):
            value = row.get(field)
            if field == "frequency_hz" and value is None:
                _require(field in row.get("missing", []), "Unknown frequency was not declared")
                unknown.append(f"qubit:{identity}:{field}")
            else:
                _require(
                    _number(value)
                    and (field != "readout_error" or value <= 1)
                    and (field not in {"t1_seconds", "t2_seconds", "frequency_hz"} or value > 0),
                    "Required qubit/readout calibration is invalid or unknown",
                )
        matrix = row.get("readout_matrix")
        _require(
            isinstance(matrix, list)
            and len(matrix) == 2
            and all(
                isinstance(line, list)
                and len(line) == 2
                and all(_number(value) and value <= 1 for value in line)
                and abs(sum(line) - 1) <= 1e-12
                for line in matrix
            ),
            "Known stochastic readout calibration is required",
        )
        _require(
            isinstance(row.get("readout_matrix_source"), str)
            and bool(row["readout_matrix_source"]),
            "Readout calibration provenance is missing",
        )
        normalized_qubits[identity] = copy.deepcopy(dict(row))
    _require(
        set(normalized_qubits) == set(subset), "Physical qubit calibration inventory is incomplete"
    )
    _require(
        snapshot.get("readout_matrix_convention") == "rows=true, columns=observed",
        "Readout calibration convention changed",
    )
    facts = {
        "backend_name": snapshot.get("backend_name"),
        "backend_width": width,
        "physical_subset": subset,
        "operation_names": sorted(operations),
        "instructions": [normalized_instructions[key] for key in sorted(normalized_instructions)],
        "qubits": [normalized_qubits[key] for key in sorted(normalized_qubits)],
        "target_timing": copy.deepcopy(dict(timing)),
        "readout_matrix_convention": snapshot["readout_matrix_convention"],
        "source": snapshot["source"],
    }
    return facts, sorted(unknown)


def validate_device_binding(
    plan: Mapping[str, Any],
    frozen_snapshot: Mapping[str, Any],
    current_snapshot: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Require the same parameters and supported options before reservation.

    This does not issue hardware approval. Callers separately validate the exact
    human approval, full local models, provider quota and persistent job ledger.
    """
    from km_quantum.m5.approvals import plan_hash

    try:
        current_time = parse_time(now or utc_now())
        digest = plan_hash(plan)
        _require(
            any(plan.get(key) == digest for key in ("plan_hash", "plan_sha256"))
            and all(key not in plan or plan[key] == digest for key in ("plan_hash", "plan_sha256")),
            "Device binding requires the exact sealed execution plan",
        )
        _require(
            plan.get("artifact_type") == "m5_hardware_execution_plan"
            and plan.get("source_kind") == "ibm_hardware"
            and plan.get("provider_verified") is True
            and plan.get("hardware_enabled") is False
            and plan.get("submission_approved") is False,
            "A frozen hardware plan with separate approval is required",
        )
        _require(
            isinstance(plan.get("backend"), str)
            and bool(plan["backend"])
            and bool(_SHA.fullmatch(str(plan.get("instance_binding", "")))),
            "Explicit plan backend and instance binding are required",
        )
        _require(
            evidence.get("provider_verified") is True
            and evidence.get("source_kind") == "ibm_hardware"
            and all(evidence.get(key) == plan.get(key) for key in ("backend", "instance_binding")),
            "Fresh provider observation differs from the plan binding",
        )
        observed = parse_time(current_snapshot.get("retrieved_at", ""))
        provider_observed = parse_time(evidence.get("obtained_at", ""))
        _require(
            all(
                0 <= (current_time - value).total_seconds() <= MAX_PREFLIGHT_AGE_SECONDS
                for value in (observed, provider_observed)
            ),
            "Fresh device/provider observation is stale or future dated",
        )
        frozen_hash, current_hash = _seal(frozen_snapshot), _seal(current_snapshot)
        _require(
            plan.get("target_snapshot_sha256") == frozen_hash,
            "Plan Target hash differs from the frozen device snapshot",
        )
        frozen_calibration = parse_time(frozen_snapshot.get("calibration_timestamp", ""))
        live_calibration = parse_time(current_snapshot.get("calibration_timestamp", ""))
        _require(
            evidence.get("calibration_timestamp") is not None
            and parse_time(evidence["calibration_timestamp"]) == live_calibration,
            "Fresh SDK/device calibration timestamps disagree",
        )
        _require(
            frozen_calibration <= live_calibration <= observed <= current_time,
            "Live calibration is future dated or older than the frozen basis",
        )
        _require(
            parse_time(frozen_snapshot.get("retrieved_at", "")) <= observed,
            "Live observation predates the frozen snapshot",
        )
        _require(
            frozen_calibration <= parse_time(frozen_snapshot.get("retrieved_at", "")),
            "Frozen calibration postdates its observation",
        )
        required_operations = {"measure"}
        if plan.get("runtime_options", {}).get("execution", {}).get("init_qubits") is True:
            required_operations.add("reset")
        for job in plan.get("provider_limit_assessment", {}).get("jobs", {}).values():
            for pub in job.get("pubs", []):
                required_operations.update(pub.get("native_gate_counts", {}))
        old_facts, unknown = _facts(frozen_snapshot, required_operations)
        live_facts, live_unknown = _facts(current_snapshot, required_operations)
        _require(
            old_facts["backend_name"] == plan["backend"]
            and old_facts["physical_subset"] == plan.get("layout"),
            "Frozen physical mapping/backend differs from the plan",
        )
        _require(
            old_facts == live_facts and unknown == live_unknown,
            "Live device parameters, mapping or timing constraints changed; a new plan is required",
        )
        repetition = evidence.get("rep_delay")
        _require(
            isinstance(repetition, Mapping)
            and repetition.get("supported") is True
            and isinstance(repetition.get("source"), str)
            and bool(repetition["source"]),
            "Current repetition-delay support is unknown",
        )
        assert isinstance(repetition, Mapping)
        limits = repetition.get("range")
        _require(
            isinstance(limits, (list, tuple))
            and len(limits) == 2
            and all(_number(value) for value in limits)
            and limits[0] <= limits[1],
            "Current repetition-delay range is invalid or unknown",
        )
        assert isinstance(limits, (list, tuple))
        execution = plan.get("runtime_options", {}).get("execution", {})
        explicit = execution.get("rep_delay")
        used = explicit if explicit is not None else repetition.get("value")
        _require(
            _number(used) and limits[0] <= used <= limits[1],
            "Plan repetition delay is no longer supported by the device",
        )
        model = plan.get("local_usage_model", {})
        _require(
            model.get("target_snapshot_sha256") == frozen_hash and bool(model.get("jobs")),
            "Local usage model is not bound to the frozen Target",
        )
        _require(
            all(
                row.get("rep_delay", {}).get("seconds") == used
                and bool(row.get("rep_delay", {}).get("source"))
                for row in model["jobs"]
            ),
            "Current repetition delay differs from the frozen usage basis",
        )
        result = {
            "artifact_type": "m5_live_device_binding_assessment",
            "schema_version": 1,
            "status": "PASS",
            "plan_hash": digest,
            "backend": plan["backend"],
            "instance_binding": plan["instance_binding"],
            "frozen_snapshot_sha256": frozen_hash,
            "current_snapshot_sha256": current_hash,
            "parameter_facts_sha256": canonical_hash(old_facts),
            "observed_at": current_snapshot["retrieved_at"],
            "provider_observed_at": evidence["obtained_at"],
            "validated_at": current_time.isoformat(),
            "frozen_calibration_timestamp": frozen_snapshot["calibration_timestamp"],
            "current_calibration_timestamp": current_snapshot["calibration_timestamp"],
            "calibration_timestamp_advanced": live_calibration > frozen_calibration,
            "parameters_unchanged": True,
            "unchanged_unknown_fields": unknown,
            "rep_delay_seconds": used,
            "rep_delay_mode": "explicit" if explicit is not None else "frozen_backend_default",
            "provider_contacted": False,
            "hardware_used": False,
            "submission_approved": False,
            "frozen_scientific_reference_changed": False,
        }
        public = dict(redact(result))
        return {**public, "content_sha256": canonical_hash(public)}
    except (KeyError, TypeError, AttributeError) as error:
        raise SafetyError(
            "Device binding has malformed or missing required facts",
            diagnostic_kind=DIAGNOSTIC_KIND,
        ) from error
    except SafetyError as error:
        if error.diagnostic_kind == DIAGNOSTIC_KIND:
            raise
        raise SafetyError(str(error), diagnostic_kind=DIAGNOSTIC_KIND) from None
    except (ValueError, OverflowError):
        raise SafetyError(
            "Device binding requires finite canonical JSON facts",
            diagnostic_kind=DIAGNOSTIC_KIND,
        ) from None
