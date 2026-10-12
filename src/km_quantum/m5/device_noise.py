"""Bounded device predictions from the pinned Aer BackendV2 noise API.

The provider circuit is never mutated.  Only physical error *attachments* are
remapped; the qubits inside a QuantumError are its local channel coordinates.
No readout error enters the unmeasured density simulation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeGuard

import numpy as np
import qiskit_aer
from numpy.typing import NDArray
from qiskit import QuantumCircuit
from qiskit.providers import BackendV2
from qiskit.quantum_info import SuperOp
from qiskit_aer import AerSimulator
from qiskit_aer.library import SaveDensityMatrix
from qiskit_aer.noise import NoiseModel, QuantumError
from threadpoolctl import threadpool_limits

from km_quantum.m4.noise import (
    ResourceLimits,
    apply_readout,
    check_density_matrix,
    resource_preflight,
)
from km_quantum.m5.mapping import marginal_register_probabilities

FloatArray = NDArray[np.float64]
AER_VERSION = "0.17.2"
MAX_LOGICAL_QUBITS = 7


@dataclass(frozen=True)
class CompactDeviceNoise:
    """Ephemeral channels, with separate physical readout and compact coverage."""

    quantum_model: NoiseModel
    quantum_attachments: frozenset[tuple[str, tuple[int, ...]]]
    readout_by_physical: dict[int, FloatArray]
    remapping: tuple[dict[str, Any], ...]


def _physical_mapping(values: Sequence[int]) -> tuple[int, ...]:
    mapping = tuple(values)
    if (
        not mapping
        or len(mapping) > 10
        or len(set(mapping)) != len(mapping)
        or any(type(value) is not int or value < 0 for value in mapping)
    ):
        raise ValueError("Compact physical mapping must have one to ten distinct physical IDs")
    return mapping


def _readout_matrix(values: Any) -> FloatArray:
    matrix = np.asarray(values, dtype=float)
    if (
        matrix.shape != (2, 2)
        or not np.all(np.isfinite(matrix))
        or np.any(matrix < 0)
        or not np.allclose(matrix.sum(axis=1), 1, rtol=0, atol=1e-9)
    ):
        raise ValueError("Readout must be a finite nonnegative row-stochastic 2x2 matrix")
    matrix.setflags(write=False)
    return matrix


def remap_backend_noise(
    full_model: NoiseModel, compact_to_physical: Sequence[int]
) -> CompactDeviceNoise:
    """Remap public serialized local attachments, preserving ordered qargs.

    BackendV2 models contain local gate errors.  A global error attachment is
    refused because it cannot prove qubit-specific calibration coverage.  Delay
    relaxation passes are not serialized by Aer's public ``to_dict``; predictions
    therefore reject circuits with delays instead of silently dropping them.
    """
    mapping = _physical_mapping(compact_to_physical)
    physical_to_compact = {physical: compact for compact, physical in enumerate(mapping)}
    model = NoiseModel(basis_gates=full_model.basis_gates)
    attachments: set[tuple[str, tuple[int, ...]]] = set()
    readout: dict[int, FloatArray] = {}
    records: list[dict[str, Any]] = []
    for serialized in full_model.to_dict(serializable=True).get("errors", []):
        if not serialized.get("gate_qubits"):
            raise ValueError("Device noise requires local physical gate_qubits attachments")
        for qargs in serialized["gate_qubits"]:
            physical = tuple(qargs)
            if not all(qubit in physical_to_compact for qubit in physical):
                continue
            compact = tuple(physical_to_compact[qubit] for qubit in physical)
            if serialized["type"] == "qerror":
                error = QuantumError.from_dict(serialized)
                # Aer 0.17.2's C++ mixture loader drops terms p<=1e-10 without
                # preserving trace. Backend two-qubit errors can contain such
                # terms (depolarization composed with both thermal channels).
                # A single Kraus instruction represents the same complete
                # channel with probability one, using only public APIs.
                original_channel = error.to_quantumchannel()
                equivalent = QuantumError(original_channel)
                equivalent_channel = SuperOp(equivalent)
                channel_delta = float(
                    np.max(np.abs(equivalent_channel.data - original_channel.data))
                )
                if channel_delta > 1e-12 or not equivalent_channel.is_cptp(atol=1e-12):
                    raise ValueError(
                        "Kraus representation changed the backend-derived CPTP channel"
                    )
                for name in serialized["operations"]:
                    if name == "measure":
                        raise ValueError(
                            "Measurement quantum errors cannot enter density prediction"
                        )
                    model.add_quantum_error(equivalent, name, compact)
                    attachments.add((name, compact))
                    records.append(
                        {
                            "type": "quantum",
                            "name": name,
                            "physical": physical,
                            "compact": compact,
                            "representation": "single_cptp_kraus_instruction",
                            "channel_max_absolute_conversion_difference": channel_delta,
                        }
                    )
            elif serialized["type"] == "roerror":
                if len(physical) != 1:
                    raise ValueError(
                        "Only documented independent single-qubit readout is supported"
                    )
                if physical[0] in readout:
                    raise ValueError("Duplicate physical readout attachment")
                readout[physical[0]] = _readout_matrix(serialized["probabilities"])
                records.append({"type": "readout", "physical": physical, "compact": compact})
            else:
                raise ValueError("Unsupported serialized device noise type")
    return CompactDeviceNoise(model, frozenset(attachments), readout, tuple(records))


def _finite_nonnegative(value: Any) -> TypeGuard[float]:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float, np.integer, np.floating))
        and bool(np.isfinite(value))
        and bool(value >= 0)
    )


def _calibration_coverage(
    backend: BackendV2,
    circuit: QuantumCircuit,
    physical: tuple[int, ...],
    measured: tuple[int, ...],
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    target = backend.target
    instruction_rows = {
        (row["name"], tuple(row["qubits"])): row for row in snapshot.get("instructions", [])
    }
    qubit_rows = {row["physical_id"]: row for row in snapshot.get("qubits", [])}
    missing: list[dict[str, Any]] = []
    gate_rows: list[dict[str, Any]] = []
    for occurrence, item in enumerate(circuit.data):
        name = item.operation.name
        if name == "barrier":
            continue
        compact_qargs = tuple(circuit.find_bit(qubit).index for qubit in item.qubits)
        qargs = tuple(physical[qubit] for qubit in compact_qargs)
        absent: list[str] = []
        if name in {"delay", "measure", "reset", "initialize"} or name.startswith("save_"):
            absent.append("unsupported_nonunitary_or_delay_instruction")
        props = target.get(name, {}).get(qargs)
        row = instruction_rows.get((name, qargs))
        if props is None:
            absent.append("target_instruction_properties")
        if row is None:
            absent.append("snapshot_instruction")
        duration = getattr(props, "duration", None)
        error = getattr(props, "error", None)
        for field, value in (("duration_seconds", duration), ("error", error)):
            if not _finite_nonnegative(value) or (field == "error" and value > 1):
                absent.append(field)
            elif row is not None and (
                not _finite_nonnegative(row.get(field))
                or not np.isclose(value, row[field], rtol=1e-12, atol=0)
            ):
                absent.append(f"snapshot_target_mismatch:{field}")
        if _finite_nonnegative(duration) and duration > 0:
            for qubit in qargs:
                qubit_props = target.qubit_properties[qubit] if target.qubit_properties else None
                calibration = qubit_rows.get(qubit, {})
                for field, attr in (("t1_seconds", "t1"), ("t2_seconds", "t2")):
                    value = getattr(qubit_props, attr, None)
                    if not _finite_nonnegative(value) or value == 0:
                        absent.append(f"{field}:{qubit}")
                    elif not _finite_nonnegative(calibration.get(field)) or not np.isclose(
                        value, calibration[field], rtol=1e-12, atol=0
                    ):
                        absent.append(f"snapshot_target_mismatch:{field}:{qubit}")
        gate_rows.append(
            {
                "occurrence": occurrence,
                "name": name,
                "physical_qargs": list(qargs),
                "compact_qargs": list(compact_qargs),
                "duration_seconds": duration,
                "reported_gate_error": error,
                "missing": absent,
            }
        )
        if absent:
            missing.append({"name": name, "physical_qargs": list(qargs), "missing": absent})
    readout_rows = []
    for compact in measured:
        qubit = physical[compact]
        row = qubit_rows.get(qubit, {})
        props = target.get("measure", {}).get((qubit,))
        absent = []
        if props is None or not _finite_nonnegative(getattr(props, "error", None)):
            absent.append("target_readout_error")
        if row.get("readout_matrix") is None:
            absent.append("snapshot_readout_matrix")
        else:
            _readout_matrix(row["readout_matrix"])
        readout_rows.append({"physical_id": qubit, "compact": compact, "missing": absent})
        if absent:
            missing.append({"physical_id": qubit, "missing": absent})
    return {
        "verified": not missing,
        "gate_occurrences": gate_rows,
        "gate_occurrence_count": len(gate_rows),
        "readout": readout_rows,
        "missing_calibrations": missing,
        "missing_calibration_policy": "BLOCKED; no unlabelled perfect or FakeBackend fallback",
    }


def build_device_noise_prediction(
    backend: BackendV2,
    compact_unmeasured: QuantumCircuit,
    compact_to_physical: Sequence[int],
    measured_compact_by_m2: Sequence[int],
    *,
    snapshot: Mapping[str, Any],
    limits: ResourceLimits | None = None,
) -> dict[str, Any]:
    """Predict the measured M2 register, preserving all active routing wires.

    Missing calibration returns ``BLOCKED`` with no invented distribution.
    Synthetic snapshots yield only an explicitly offline readiness result.
    Only the at-most-128-entry logical probability vectors are returned; the
    at-most-1024-entry compact diagonal and density matrix remain ephemeral.
    """
    if qiskit_aer.__version__ != AER_VERSION:
        raise ValueError(f"M5 device prediction requires pinned Aer {AER_VERSION}")
    if not isinstance(backend, BackendV2):
        raise ValueError("M5 device prediction requires a BackendV2 Target")
    physical = _physical_mapping(compact_to_physical)
    measured = tuple(measured_compact_by_m2)
    if (
        not measured
        or len(measured) > MAX_LOGICAL_QUBITS
        or len(set(measured)) != len(measured)
        or any(type(qubit) is not int or not 0 <= qubit < len(physical) for qubit in measured)
    ):
        raise ValueError(
            "Measured mapping requires distinct compact qubits for one to seven M2 bits"
        )
    if compact_unmeasured.num_qubits != len(physical):
        raise ValueError("Compact circuit width differs from its physical mapping")
    if compact_unmeasured.parameters or compact_unmeasured.num_clbits:
        raise ValueError("Device density prediction requires a bound unmeasured compact circuit")
    limits = limits or ResourceLimits()
    resources = resource_preflight(compact_unmeasured.num_qubits, limits)
    coverage = _calibration_coverage(backend, compact_unmeasured, physical, measured, snapshot)
    source = snapshot.get("source", snapshot.get("artifact_source"))
    if source not in {"offline_synthetic", "offline_test_backend", "provider"}:
        raise ValueError("Snapshot must explicitly declare its provider or offline test source")
    backend_module = backend.__class__.__module__.lower()
    if source == "provider" and (
        getattr(backend, "snapshot_source", None) in {"offline_synthetic", "offline_test_backend"}
        or "fake_provider" in backend_module
        or backend_module.startswith("qiskit_aer")
    ):
        raise ValueError("Synthetic, fake and simulator backends cannot claim device calibration")
    metadata: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "m5_device_noise_prediction",
        "status": "READY" if source == "provider" else "OFFLINE_READY",
        "snapshot_source": source,
        "synthetic_model_is_device_calibration": False,
        "device_calibration_used": source == "provider",
        "hardware_used": False,
        "snapshot_sha256": hashlib.sha256(
            json.dumps(
                dict(snapshot), sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest(),
        "snapshot_retrieved_at": snapshot.get("retrieved_at"),
        "snapshot_calibration_timestamp": snapshot.get("calibration_timestamp"),
        "missing_snapshot_timestamps": [
            field for field in ("retrieved_at", "calibration_timestamp") if not snapshot.get(field)
        ],
        "compact_to_physical": list(physical),
        "measured_compact_by_m2": list(measured),
        "measured_physical_by_m2": [physical[qubit] for qubit in measured],
        "active_simulated_physical_width": len(physical),
        "logical_register_width": len(measured),
        "resources": resources,
        "coverage": coverage,
        "probabilities_before_readout": None,
        "probabilities_after_readout": None,
        "noise_model_api": "qiskit_aer.noise.NoiseModel.from_backend",
        "aer_version": AER_VERSION,
        "noise_model_options": {
            "gate_error": True,
            "readout_error": True,
            "thermal_relaxation": True,
            "temperature": 0,
        },
        "additional_m4_depolarization": False,
        "quantum_channel_representation": "equivalent single Kraus instruction per local error",
        "quantum_channel_conversion_api": "QuantumError(error.to_quantumchannel())",
        "quantum_channel_equivalence_atol": 1e-12,
        "quantum_channel_cptp_verified": False,
        "small_probability_mixture_terms_preserved": True,
        "readout_applications": 0,
        "idle_delay_effects_modelled": False,
        "approximation": (
            "Aer backend-derived gate-error and in-gate T1/T2 channels plus independent readout; "
            "no complete idle schedule, crosstalk or drift model. Aer may truncate T2 to 2*T1."
        ),
        "no_density_or_full_device_vector_archived": True,
    }
    if not coverage["verified"]:
        metadata["status"] = "BLOCKED"
        return metadata
    full_model = NoiseModel.from_backend(backend, **metadata["noise_model_options"])
    compact_noise = remap_backend_noise(full_model, physical)
    metadata["quantum_channel_cptp_verified"] = True
    metadata["quantum_channel_max_absolute_conversion_difference"] = max(
        (
            record["channel_max_absolute_conversion_difference"]
            for record in compact_noise.remapping
            if record["type"] == "quantum"
        ),
        default=0.0,
    )
    qubit_rows = {row["physical_id"]: row for row in snapshot["qubits"]}
    readout_matrices = []
    for compact in measured:
        physical_id = physical[compact]
        snapshot_matrix = _readout_matrix(qubit_rows[physical_id]["readout_matrix"])
        model_matrix = compact_noise.readout_by_physical.get(physical_id, np.eye(2))
        if not np.allclose(snapshot_matrix, model_matrix, rtol=0, atol=1e-12):
            raise ValueError("Snapshot readout matrix differs from the frozen BackendV2 Aer model")
        readout_matrices.append(model_matrix)
    for row in coverage["gate_occurrences"]:
        attachment = (row["name"], tuple(row["compact_qargs"]))
        row["quantum_channel_attached"] = attachment in compact_noise.quantum_attachments
        # Aer elides an exactly ideal error.  It must still have explicit zero
        # error and zero duration calibration, rather than missing properties.
        row["explicitly_ideal_calibration"] = (
            row["reported_gate_error"] == 0 and row["duration_seconds"] == 0
        )
        if not row["quantum_channel_attached"] and not row["explicitly_ideal_calibration"]:
            raise ValueError("Aer device noise does not cover an active calibrated gate occurrence")
    coverage["quantum_channel_applications"] = sum(
        row["quantum_channel_attached"] for row in coverage["gate_occurrences"]
    )
    coverage["readout_in_density_model"] = False
    coverage["physical_to_compact_remapping"] = list(compact_noise.remapping)
    options = {
        "method": "density_matrix",
        "device": "CPU",
        "precision": "double",
        "max_memory_mb": limits.max_memory_mb,
        "max_parallel_threads": limits.max_parallel_threads,
        "max_parallel_experiments": 1,
        "max_parallel_shots": 1,
        "enable_truncation": False,
        "zero_threshold": 1e-14,
        "fusion_enable": False,
    }
    saved = compact_unmeasured.copy()
    saved.append(SaveDensityMatrix(saved.num_qubits, label="rho"), saved.qubits)
    with threadpool_limits(limits=limits.max_parallel_threads):
        result = (
            AerSimulator(noise_model=compact_noise.quantum_model, **options)
            .run(saved, shots=1, seed_simulator=0)
            .result()
        )
        if not result.success or not result.results[0].success:
            raise ValueError(f"Device density simulation failed: {result.status}")
        compact_probabilities, physicality = check_density_matrix(
            np.asarray(result.data(0)["rho"].data, dtype=complex),
            saved.num_qubits,
            max_parallel_threads=limits.max_parallel_threads,
        )
    before = marginal_register_probabilities(compact_probabilities, measured)
    # Only measured qubits receive readout, in their actual M2 measurement order.
    # Independent readout commutes with marginalizing unmeasured routing wires.
    after = apply_readout(before, readout_matrices)
    metadata.update(
        {
            "probabilities_before_readout": before.tolist(),
            "probabilities_after_readout": after.tolist(),
            "density_physicality": physicality,
            "simulation_options": options,
            "simulation_seed": 0,
            "snapshot_execution_shots": 1,
            "shots_are_not_a_density_estimation_budget": True,
            "measurement_in_density_simulation": False,
            "readout_applications": 1,
            "readout_matrices_m2_order": [matrix.tolist() for matrix in readout_matrices],
            "readout_matrix_sources_m2_order": [
                qubit_rows[physical[compact]].get("readout_matrix_source") for compact in measured
            ],
            "readout_convention": "rows=true; columns=observed; p_observed=R.T@p_true",
        }
    )
    return metadata
