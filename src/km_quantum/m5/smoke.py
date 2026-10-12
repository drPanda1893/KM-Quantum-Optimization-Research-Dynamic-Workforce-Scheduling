"""Descriptive smoke controls with hard identity and frozen-mapping checks.

Wrong observed outcomes remain valid data. No empirical success threshold is
defined here, and this report grants neither hardware approval nor M5 PASS.
The four logical basis controls, their measured mappings and predictions are
loaded by persistent plan identity from already frozen local artifacts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import binomtest

from km_quantum.m5.approvals import plan_hash
from km_quantum.m5.circuits import compiled_ideal_probabilities, logical_probabilities
from km_quantum.m5.io import canonical_hash, read_sealed
from km_quantum.m5.mapping import decode_classical_counts, mapping_from_compiled
from km_quantum.m5.provider import SafetyError
from km_quantum.m5.snapshots import load_qpy
from km_quantum.m5.workflow import (
    artifact_path,
    restore_compilation,
    verify_plan_inventory_binding,
)
from km_quantum.qaoa.ordering import m2_bits_to_basis_index

_PATTERNS = {"0000000", "1111111", "1001010", "0110001"}


def _vector(values: Any, width: int) -> np.ndarray[Any, np.dtype[np.float64]]:
    vector = np.asarray(values, dtype=np.float64)
    if (
        vector.shape != (2**width,)
        or not np.all(np.isfinite(vector))
        or np.any(vector < 0)
        or not np.isclose(vector.sum(), 1, rtol=0, atol=1e-10)
    ):
        raise ValueError("Smoke prediction requires a complete normalized probability vector")
    return vector


def _control(
    root: Path, output: Path, record: dict[str, Any], prepared: Mapping[str, Any]
) -> dict[str, Any]:
    identity = record["circuit_id"]
    if read_sealed(output / "circuits" / identity / "record.json") != record:
        raise ValueError("Frozen smoke record differs from its prepared inventory")
    compilation = restore_compilation(root, output, record)
    mapping = compilation.mapping
    logical_meta = record["qpy"]["logical"]
    logical = load_qpy(
        artifact_path(root, logical_meta["path"], output), expected_sha256=logical_meta["sha256"]
    )
    metadata = logical.metadata or {}
    pattern = metadata.get("expected_m2_bits")
    if (
        metadata.get("artifact_type") != "m5_smoke_basis_control"
        or not isinstance(pattern, str)
        or pattern not in _PATTERNS
        or logical.num_qubits != 7
        or logical.num_clbits
        or logical.num_parameters
        or record["num_bits"] != 7
        or record["equivalence"].get("passed") is not True
        or record["equivalence"].get("expected_m2_bits") != pattern
        or mapping.register_name != "m2"
        or mapping.m2_to_logical != tuple(range(7))
    ):
        raise ValueError("Smoke basis metadata or logical bit convention changed")
    actual_mapping = mapping_from_compiled(
        compilation.provider_circuit,
        mapping.logical_to_initial_physical,
        m2_to_classical=mapping.m2_to_classical,
    )
    if (
        actual_mapping != mapping
        or tuple(record["mapping"]["m2_to_compact"]) != mapping.m2_to_compact
        or compilation.compact_circuit.num_qubits > 10
    ):
        raise ValueError("Frozen smoke mapping differs from actual terminal measurements")
    prediction = read_sealed(output / "predictions" / f"{identity}.json")
    if (
        prediction["circuit_id"] != identity
        or prediction["content_sha256"] != record["prediction_sha256"]
        or prediction["target_snapshot_sha256"] != prepared["target_snapshot_sha256"]
        or prediction["source_kind"] != prepared["source_kind"]
        or prediction.get("hardware_used") is not False
        or prediction.get("frozen_before_results") is not True
        or prediction["device_noise"].get("status") not in {"READY", "OFFLINE_READY"}
    ):
        raise ValueError("Smoke prediction identity, provenance or frozen calibration changed")
    expected = np.zeros(128)
    index = m2_bits_to_basis_index(pattern)
    expected[index] = 1
    ideal = _vector(prediction["logical_ideal"], 7)
    compiled = _vector(prediction["compiled_ideal"], 7)
    if not all(
        np.allclose(values, expected, rtol=0, atol=1e-10)
        for values in (ideal, compiled, logical_probabilities(logical))
    ) or not np.allclose(compiled_ideal_probabilities(compilation), compiled, rtol=0, atol=1e-10):
        raise ValueError("Frozen smoke ideal QPY does not prepare its declared M2 pattern")
    device = _vector(prediction["device_noise"]["probabilities_after_readout"], 7)
    classical = ["0"] * 7
    for variable, position in enumerate(mapping.m2_to_classical):
        classical[position] = pattern[variable]
    return {
        "circuit_id": identity,
        "expected_m2_bits": pattern,
        "expected_displayed_bits": "".join(reversed(classical)),
        "m2_to_classical": list(mapping.m2_to_classical),
        "measured_physical_by_m2": list(mapping.m2_to_final_physical),
        "measured_compact_by_m2": list(mapping.m2_to_compact),
        "logical_qpy_sha256": logical_meta["sha256"],
        "provider_qpy_sha256": record["qpy"]["provider"]["sha256"],
        "prediction_sha256": prediction["content_sha256"],
        "ideal_expected_pattern_probability": float(ideal[index]),
        "compiled_ideal_expected_pattern_probability": float(compiled[index]),
        "device_post_readout_expected_pattern_probability": float(device[index]),
        "prediction_is_model_not_measurement": True,
        "structural_check_passed": True,
    }


def _analyze(
    root: Path,
    output: Path,
    plan: Mapping[str, Any],
    prepared: Mapping[str, Any],
    raw_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if plan.get("plan_hash") != plan_hash(plan):
        raise ValueError("Smoke report requires the exact persistent execution plan hash")
    if read_sealed(output / "prepared_manifest.json") != dict(prepared):
        raise ValueError("Smoke prepared inventory differs from its sealed artifact")
    verify_plan_inventory_binding(dict(plan), dict(prepared))
    records = {record["circuit_id"]: record for record in prepared["records"]}
    smoke_jobs = [job for job in plan["jobs"] if job["kind"] == "smoke"]
    if len(smoke_jobs) != 1 or len(smoke_jobs[0]["pubs"]) != 4:
        raise ValueError("Exactly one four-PUB smoke job is required")
    smoke = smoke_jobs[0]
    expected_pubs = {pub["pub_id"]: (job, pub) for job in plan["jobs"] for pub in job["pubs"]}
    observed: dict[str, Mapping[str, Any]] = {}
    for row in raw_results:
        pub_id = row.get("pub_id")
        if not isinstance(pub_id, str) or pub_id not in expected_pubs or pub_id in observed:
            raise ValueError("Smoke input contains an unknown or duplicate persistent PUB identity")
        job, pub = expected_pubs[pub_id]
        if (
            any(
                row.get(key) != pub.get(key)
                for key in ("circuit_id", "circuit_hash", "shots", "num_bits", "block", "case_name")
            )
            or row.get("job_id") != job["id"]
        ):
            raise ValueError(
                "Collected PUB identity, job, shots or bit width differs from the plan"
            )
        if (
            type(row.get("shots")) is not int
            or type(row.get("num_bits")) is not int
            or row.get("circuit_sha256", pub["circuit_hash"]) != pub["circuit_hash"]
            or not isinstance(row.get("counts"), Mapping)
            or row.get("source") not in {"hardware", "mock"}
            or row.get("hardware_used") is not (row.get("source") == "hardware")
        ):
            raise ValueError("Collected PUB hash, counts or hardware/mock provenance is invalid")
        if row["source"] == "hardware" and (
            plan.get("provider_verified") is not True or prepared.get("source_kind") != "provider"
        ):
            raise ValueError("Hardware-source smoke data requires a verified real hardware plan")
        decode_classical_counts(
            row["counts"],
            records[pub["circuit_id"]]["mapping"]["m2_to_classical"],
            shots=pub["shots"],
        )
        observed[pub_id] = row
    controls = [
        _control(root, output, records[pub["circuit_id"]], prepared) for pub in smoke["pubs"]
    ]
    if {control["expected_m2_bits"] for control in controls} != _PATTERNS:
        raise ValueError("Smoke job must contain each of the four distinct frozen basis patterns")
    sources = {
        observed[pub["pub_id"]]["source"] for pub in smoke["pubs"] if pub["pub_id"] in observed
    }
    if len(sources) > 1:
        raise ValueError("One smoke job cannot mix hardware and mock PUB provenance")
    source = next(iter(sources), "not_available")
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for pub, control in zip(smoke["pubs"], controls, strict=True):
        pub_id = pub["pub_id"]
        observed_row = observed.get(pub_id)
        detail = {"pub_id": pub_id, "job_id": smoke["id"], "shots": pub["shots"], **control}
        if pub["shots"] != 128 or pub["num_bits"] != 7:
            raise ValueError("Frozen smoke PUBs require exactly seven bits and 128 shots each")
        if observed_row is None:
            missing.append(pub_id)
            detail.update(result_available=False, raw_counts=None, m2_decoded_counts=None)
        else:
            decoded = decode_classical_counts(
                observed_row["counts"], control["m2_to_classical"], shots=pub["shots"]
            )
            count = decoded.get(control["expected_m2_bits"], 0)
            frequency = count / pub["shots"]
            interval = binomtest(count, pub["shots"]).proportion_ci(
                confidence_level=0.95, method="exact"
            )
            detail.update(
                result_available=True,
                source=observed_row["source"],
                hardware_used=observed_row["hardware_used"],
                raw_counts=deepcopy(dict(observed_row["counts"])),
                m2_decoded_counts=decoded,
                expected_pattern_count=count,
                expected_pattern_frequency=frequency,
                expected_pattern_confidence_interval_95=[float(interval.low), float(interval.high)],
                confidence_interval_method="Clopper-Pearson exact two-sided binomial",
                confidence_interval_assumption="Independent Bernoulli shots within this control",
                zero_hits_implies_zero_probability=False,
                frequency_minus_ideal_percentage_points=100
                * (frequency - control["ideal_expected_pattern_probability"]),
                frequency_minus_device_prediction_percentage_points=100
                * (frequency - control["device_post_readout_expected_pattern_probability"]),
            )
        rows.append(detail)
    complete = not missing
    return {
        "schema_version": 1,
        "artifact_type": "m5_descriptive_smoke_controls",
        "status": "COMPLETE"
        if complete and source == "hardware"
        else "TEST_DATA_ONLY"
        if complete
        else "INCOMPLETE",
        "source_kind": source,
        "hardware_used": source == "hardware",
        "m5_hardware_pass": False,
        "plan_hash": plan_hash(plan),
        "prepared_manifest_sha256": prepared["content_sha256"],
        "job_id": smoke["id"],
        "structural_passed": complete,
        "ready_for_next_job": complete,
        "ready_for_next_job_scope": "Structural contract only; no hardware submission authority",
        "expected_pub_count": 4,
        "observed_smoke_pub_count": 4 - len(missing),
        "missing_pub_ids": missing,
        "expected_shots": 512,
        "observed_shots": sum(row["shots"] for row in rows if row["result_available"]),
        "controls": rows,
        "raw_counts_all_retained": True,
        "postselection_applied": False,
        "observed_agreement_is_execution_gate": False,
        "statistical_quality_threshold": None,
        "statistical_quality_gate_defined": False,
        "bit_conventions": {
            "m2": "Character i denotes variable i; basis index uses weight 2**i",
            "displayed_counts": "Qiskit displays high classical bits first; bit 0 is on the right",
        },
        "limitations": [
            "These basis controls describe observed bit patterns; they are not state fidelity",
            "No predeclared statistical quality threshold exists; wrong outcomes remain valid data",
            "Model disagreement does not by itself establish a mapping or technical failure",
            "Four controls do not establish stability, drift or generalization",
        ],
    }


def analyze_smoke_controls(
    root: Path,
    output: Path,
    plan: Mapping[str, Any],
    prepared: Mapping[str, Any],
    raw_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Read frozen controls and describe actual retained counts, without writes.

    Missing smoke PUBs yield INCOMPLETE with a failed structural readiness gate.
    Unknown identities, tampered artifacts and malformed counts raise SafetyError.
    Cumulative results may also contain known main PUBs; those are identity and
    count validated but do not enter this separate four-control report.
    """
    try:
        report = _analyze(root.resolve(), output.resolve(), plan, prepared, raw_results)
        return {**report, "content_sha256": canonical_hash(report)}
    except (ValueError, KeyError, TypeError, OSError) as error:
        if isinstance(error, SafetyError):
            raise
        raise SafetyError(
            f"Smoke control contract failed: {error}",
            diagnostic_kind="SMOKE_CONTROL_CONTRACT_ERROR",
        ) from None
