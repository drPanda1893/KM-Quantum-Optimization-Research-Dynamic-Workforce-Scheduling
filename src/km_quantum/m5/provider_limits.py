"""Documented Sampler service limits and concrete, offline payload assessment.

The unpublished server input-byte ceiling stays unknown. A separate 1 MiB
client guard bounds this experiment's serialized payload; it is no IBM limit.
No account, backend or Runtime service is constructed by this module.
"""

from __future__ import annotations

import copy
import importlib
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from km_quantum.m4.io import canonical_hash

LIMITS_URL = "https://quantum.cloud.ibm.com/docs/en/guides/job-limits"
REST_URL = "https://quantum.cloud.ibm.com/docs/en/api/qiskit-runtime-rest/tags/jobs"
DOCS_RETRIEVED_AT = "2026-10-12T11:58:24+00:00"
CONTRACT = "m5_ibm_sampler_limits_v1"
LOCAL_MAX_PAYLOAD_BYTES = 1024 * 1024
LOCAL_ENVELOPE_ALLOWANCE_BYTES = 1024
_WEIGHTS = {
    "rz": 1,
    "delay": 1,
    "sx": 2,
    "x": 2,
    "cx": 5,
    "cz": 5,
    "ecr": 5,
    "measure": 10,
    "reset": 17,
    "init": 50,
}


class LimitContractError(ValueError):
    """Unknown expansion, unsupported operation, changed bytes or missing facts."""


def service_limit_policy() -> dict[str, Any]:
    """Frozen documentary policy, not fabricated live backend metadata."""
    value: dict[str, Any] = {
        "contract": CONTRACT,
        "scope": "IBM Sampler V2 classified job; all expanded PUBs; optional processing disabled",
        "sources": [
            {"url": LIMITS_URL, "retrieved_at": DOCS_RETRIEVED_AT},
            {"url": REST_URL, "retrieved_at": DOCS_RETRIEVED_AT},
        ],
        "max_executions_per_job": 10_000_000,
        "max_low_level_instructions_per_qubit": 26_800_000,
        "max_rz_per_circuit": 30_000_000,
        "max_sx_per_circuit": 20_000_000,
        "max_two_qubit_gates_per_circuit": 5_000_000,
        "low_level_weights": dict(_WEIGHTS),
        "rate_limit": {"jobs": 5, "seconds": 60, "scope": "per user, submission admission"},
        "campaign_total_jobs": {"value": None, "status": "not_exposed"},
        "concurrent_jobs": {"value": None, "status": "not_exposed"},
        "payload": {
            "provider_max_bytes": None,
            "provider_status": "not_exposed",
            "local_max_bytes": LOCAL_MAX_PAYLOAD_BYTES,
            "local_policy": "M5 client guard, not an IBM service ceiling",
            "optional_sdk_envelope_allowance_bytes": LOCAL_ENVELOPE_ALLOWANCE_BYTES,
        },
        "legacy_configuration_limits_enforced": False,
    }
    return {**value, "policy_sha256": canonical_hash(value)}


def validate_service_limit_policy(policy: Mapping[str, Any]) -> None:
    if dict(policy) != service_limit_policy():
        raise LimitContractError(
            "Service limit policy differs from the documented reviewed contract"
        )


def plan_payload_hash(plan: Mapping[str, Any]) -> str:
    """Bind execution-relevant content without circular assessment/plan seals."""
    value = {
        key: copy.deepcopy(plan.get(key))
        for key in (
            "backend",
            "instance_binding",
            "layout",
            "runtime_options",
            "budget",
            "jobs",
            "prepared_manifest_sha256",
            "selection_manifest_sha256",
            "source_context_sha256",
            "target_snapshot_sha256",
        )
    }
    for job in value["jobs"] or []:
        for key in ("estimated_qpu_seconds", "estimate_source", "usage_model_sha256"):
            job.pop(key, None)
    value["circuits"] = {
        key: {
            "qpy_sha256": row["qpy_sha256"],
            "num_bits": row["num_bits"],
            "mapping": row.get("mapping"),
        }
        for key, row in sorted(plan["circuits"].items())
    }
    return canonical_hash(value)


def _runtime_payload_bytes(plan: Mapping[str, Any], job: Mapping[str, Any], pubs: list[Any]) -> int:
    SamplerOptions = importlib.import_module("qiskit_ibm_runtime.options").SamplerOptions
    RuntimeEncoder = importlib.import_module("qiskit_ibm_runtime.utils.json").RuntimeEncoder

    options = SamplerOptions(**dict(plan["runtime_options"]))
    inputs = {"pubs": pubs, **SamplerOptions._get_program_inputs(asdict(options))}
    # Deterministic tags have fixed-size hash portions. Placeholder hash lengths
    # match ledger tags; their content cannot affect uncompressed JSON length.
    payload = {
        "program_id": "sampler",
        "backend": plan.get("backend"),
        "params": inputs,
        "cost": job["max_execution_time"],
        "tags": ["m5-" + "0" * 16 + "-" + job["id"], "m5-plan-" + "0" * 64],
    }
    return len(json.dumps(payload, cls=RuntimeEncoder).encode("utf-8"))


def count_sampler_executions(pubs: list[Any]) -> dict[str, Any]:
    """Use real SamplerPub broadcasting rules; never equate PUBs with executions."""
    from qiskit.primitives.containers import SamplerPub

    coerced = [SamplerPub.coerce(pub) for pub in pubs]
    expanded = sum(pub.size for pub in coerced)
    if any(type(pub.shots) is not int or pub.shots < 1 for pub in coerced):
        raise LimitContractError(
            "Every expanded Sampler PUB requires explicit positive integer shots"
        )
    shots = sum(pub.size * pub.shots for pub in coerced)
    return {
        "pub_count": len(coerced),
        "broadcasted_circuits": expanded,
        "executions": shots,
        "pub_shapes": [list(pub.shape) for pub in coerced],
    }


def assess_plan_limits(
    plan: Mapping[str, Any],
    circuits: Mapping[str, Any],
    *,
    qpy_hashes: Mapping[str, str],
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assess each exact native PUB with explicit per-PUB shots, offline only.

    Low-level memory weights are conservatively counted for every shot rather
    than assuming undocumented loop-memory reuse. Normal initialization adds
    50 control instructions per shot on every provider wire. This deliberately
    includes idle device wires without simulating them.
    """
    from qiskit.primitives.containers import SamplerPub

    selected = service_limit_policy() if policy is None else dict(policy)
    validate_service_limit_policy(selected)
    if not plan.get("jobs") or len({job["id"] for job in plan["jobs"]}) != len(plan["jobs"]):
        raise LimitContractError("Distinct persistent job identities are required")
    rows: dict[str, Any] = {}
    for job in plan["jobs"]:
        missing: list[str] = []
        pub_rows = []
        all_pubs: list[Any] = []
        low_level: Counter[int] = Counter()
        if not job["pubs"]:
            raise LimitContractError("A Sampler job requires its exact nonempty PUB inventory")
        for pub in job["pubs"]:
            identity = pub["circuit_id"]
            expected = plan["circuits"][identity]["qpy_sha256"]
            if qpy_hashes.get(identity) != expected or pub["circuit_hash"] != expected:
                raise LimitContractError("Loaded QPY hash differs from the exact execution plan")
            circuit = circuits[identity].copy()
            if type(pub["shots"]) is not int or pub["shots"] < 1:
                raise LimitContractError("Exact positive per-PUB shots are required")
            mapping = plan["circuits"][identity].get("mapping", {})
            if mapping.get("provider_width", circuit.num_qubits) != circuit.num_qubits:
                raise LimitContractError(
                    "Frozen provider qubit width differs from the concrete circuit"
                )
            if circuit.num_parameters or pub.get("parameter_values") is not None:
                raise LimitContractError(
                    "M5 permits frozen bound circuits without parameter expansion"
                )
            registers = [register for register in circuit.cregs if register.name == "m2"]
            if len(registers) != 1 or registers[0].size != pub["num_bits"]:
                raise LimitContractError("Exact named m2 register differs from the PUB")
            metadata = dict(circuit.metadata or {})
            metadata.update(m5_pub_id=pub["pub_id"], m5_circuit_hash=expected)
            circuit.metadata = metadata
            coerced = SamplerPub.coerce((circuit, None, pub["shots"]))
            if coerced.shape != () or coerced.size != 1:
                raise LimitContractError("Unexpected M5 PUB expansion")
            all_pubs.append(coerced)
            counts: Counter[str] = Counter()
            two_qubit = 0
            for instruction in circuit.data:
                name = instruction.operation.name
                if name == "barrier":
                    continue  # Compiler directive, not a native control operation.
                if name not in _WEIGHTS or instruction.operation.num_qubits > 2:
                    missing.append("Undocumented native control weight: " + name)
                    continue
                counts[name] += 1
                two_qubit += instruction.operation.num_qubits == 2
                for qubit in instruction.qubits:
                    low_level[circuit.find_bit(qubit).index] += _WEIGHTS[name] * pub["shots"]
            if plan["runtime_options"]["execution"]["init_qubits"]:
                for qubit in range(circuit.num_qubits):
                    low_level[qubit] += _WEIGHTS["init"] * pub["shots"]
            if counts["rz"] > selected["max_rz_per_circuit"]:
                missing.append("RZ count exceeds documented per-circuit limit")
            if counts["sx"] > selected["max_sx_per_circuit"]:
                missing.append("SX count exceeds documented per-circuit limit")
            if two_qubit > selected["max_two_qubit_gates_per_circuit"]:
                missing.append("Two-qubit count exceeds documented per-circuit limit")
            pub_rows.append(
                {
                    "pub_id": pub["pub_id"],
                    "circuit_id": identity,
                    "circuit_hash": expected,
                    "shots": pub["shots"],
                    "num_bits": pub["num_bits"],
                    "shape": [],
                    "native_gate_counts": dict(counts),
                    "two_qubit_gates": two_qubit,
                    "provider_qubits": circuit.num_qubits,
                }
            )
        expansion = count_sampler_executions(all_pubs)
        if expansion["executions"] > selected["max_executions_per_job"]:
            missing.append("Expanded Sampler executions exceed documented job limit")
        if any(
            value > selected["max_low_level_instructions_per_qubit"] for value in low_level.values()
        ):
            missing.append("Control instructions exceed documented per-qubit job limit")
        payload_bytes = _runtime_payload_bytes(plan, job, all_pubs)
        guarded_bytes = payload_bytes + LOCAL_ENVELOPE_ALLOWANCE_BYTES
        if guarded_bytes > selected["payload"]["local_max_bytes"]:
            missing.append("Serialized native payload exceeds the separate 1 MiB local guard")
        rows[job["id"]] = {
            "status": "PASS" if not missing else "BLOCKED",
            "missing": sorted(set(missing)),
            **expansion,
            "pubs": pub_rows,
            "per_qubit_low_level_instructions": {
                str(key): value for key, value in sorted(low_level.items())
            },
            "control_count_policy": "Local conservative shot-weighted bound; init on every wire",
            "payload_bytes": payload_bytes,
            "guarded_payload_bytes": guarded_bytes,
            "optional_sdk_envelope_allowance_bytes": LOCAL_ENVELOPE_ALLOWANCE_BYTES,
            "payload_encoder": "qiskit-ibm-runtime 0.47 RuntimeEncoder and SamplerOptions",
            "provider_payload_ceiling": "not_exposed; no acceptance guarantee",
            "circuit_hashes": {pub["circuit_id"]: pub["circuit_hash"] for pub in job["pubs"]},
        }
    result = {
        "artifact_type": "m5_provider_limit_assessment",
        "schema_version": 1,
        "status": "PASS" if all(row["status"] == "PASS" for row in rows.values()) else "BLOCKED",
        "hardware_used": False,
        "provider_contacted": False,
        "policy_sha256": selected["policy_sha256"],
        "plan_payload_sha256": plan_payload_hash(plan),
        "jobs": rows,
        "limitations": [
            "Unpublished service payload ceiling and concurrency limits remain unknown",
            "Service scheduling and runtime internal sub-jobs are not a new provider job",
        ],
    }
    return {**result, "content_sha256": canonical_hash(result)}


def validate_limit_assessment(plan: Mapping[str, Any], policy: Mapping[str, Any]) -> None:
    validate_service_limit_policy(policy)
    assessment = plan.get("provider_limit_assessment", {})
    if not isinstance(assessment, Mapping):
        raise LimitContractError("Concrete provider-limit assessment is missing")
    payload = {key: value for key, value in assessment.items() if key != "content_sha256"}
    if (
        assessment.get("artifact_type") != "m5_provider_limit_assessment"
        or assessment.get("schema_version") != 1
        or assessment.get("hardware_used") is not False
        or assessment.get("provider_contacted") is not False
        or assessment.get("content_sha256") != canonical_hash(payload)
        or plan.get("provider_limit_assessment_sha256") != assessment.get("content_sha256")
        or assessment.get("status") != "PASS"
        or assessment.get("policy_sha256") != policy["policy_sha256"]
        or assessment.get("plan_payload_sha256") != plan_payload_hash(plan)
        or set(assessment.get("jobs", {})) != {job["id"] for job in plan["jobs"]}
    ):
        raise LimitContractError(
            "Concrete provider-limit assessment is missing, changed or blocked"
        )
    for job in plan["jobs"]:
        row = assessment["jobs"][job["id"]]
        expected = {pub["circuit_id"]: pub["circuit_hash"] for pub in job["pubs"]}
        if (
            row.get("status") != "PASS"
            or row.get("missing")
            or row.get("circuit_hashes") != expected
            or row.get("pub_count") != len(job["pubs"])
            or row.get("broadcasted_circuits") != len(job["pubs"])
            or row.get("executions") != sum(pub["shots"] for pub in job["pubs"])
            or row["executions"] > policy["max_executions_per_job"]
            or type(row.get("payload_bytes")) is not int
            or row["payload_bytes"] < 1
            or row.get("optional_sdk_envelope_allowance_bytes") != LOCAL_ENVELOPE_ALLOWANCE_BYTES
            or row.get("guarded_payload_bytes")
            != row["payload_bytes"] + LOCAL_ENVELOPE_ALLOWANCE_BYTES
            or row["guarded_payload_bytes"] > LOCAL_MAX_PAYLOAD_BYTES
            or not row.get("per_qubit_low_level_instructions")
            or any(
                type(value) is not int or value < 0
                for value in row.get("per_qubit_low_level_instructions", {}).values()
            )
            or any(
                value > policy["max_low_level_instructions_per_qubit"]
                for value in row.get("per_qubit_low_level_instructions", {}).values()
            )
        ):
            raise LimitContractError("Concrete native PUB/job limits do not match the frozen plan")
        if len(row.get("pubs", [])) != len(job["pubs"]):
            raise LimitContractError("Native PUB assessment inventory is incomplete")
        for actual, frozen in zip(row["pubs"], job["pubs"], strict=True):
            if (
                any(
                    actual.get(key) != frozen[key]
                    for key in ("pub_id", "circuit_id", "circuit_hash", "shots", "num_bits")
                )
                or actual.get("shape") != []
                or type(actual.get("provider_qubits")) is not int
                or actual["provider_qubits"] < frozen["num_bits"]
                or type(actual.get("two_qubit_gates")) is not int
                or not 0 <= actual["two_qubit_gates"] <= policy["max_two_qubit_gates_per_circuit"]
                or any(
                    key not in _WEIGHTS or type(value) is not int or value < 0
                    for key, value in actual.get("native_gate_counts", {}).items()
                )
                or actual.get("native_gate_counts", {}).get("rz", 0) > policy["max_rz_per_circuit"]
                or actual.get("native_gate_counts", {}).get("sx", 0) > policy["max_sx_per_circuit"]
            ):
                raise LimitContractError(
                    "Native circuit/PUB assessment differs from the frozen plan"
                )
