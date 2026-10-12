"""Local critical-path components from archived, public SI instruction durations.

This does not reconstruct a full live Target: the archived snapshot lacks its
complete timing/alignment context. No scheduling delays are inserted into the
provider or noise circuit, and no account, primitive or backend is constructed.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from qiskit import QuantumCircuit

ARCHIVED_DURATION_ATOL_SECONDS = 1e-15
_UNSUPPORTED = {
    "delay",
    "if_else",
    "while_loop",
    "for_loop",
    "switch_case",
    "break_loop",
    "continue_loop",
}


def _seconds(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _duration_index(snapshot: Mapping[str, Any]) -> dict[tuple[str, tuple[int, ...]], list[Any]]:
    index: dict[tuple[str, tuple[int, ...]], list[Any]] = defaultdict(list)
    rows = snapshot.get("instructions", [])
    if not isinstance(rows, (list, tuple)):
        return index
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        name, qubits = row.get("name"), row.get("qubits")
        if (
            not isinstance(name, str)
            or not isinstance(qubits, (list, tuple))
            or any(type(qubit) is not int or qubit < 0 for qubit in qubits)
        ):
            continue
        index[(name, tuple(qubits))].append(row.get("duration_seconds"))
    return index


def _critical_seconds(
    nodes: list[tuple[str, tuple[int, ...], tuple[int, ...], float | None]],
    num_qubits: int,
    num_clbits: int,
    *,
    readout: bool,
) -> float | None:
    """Traverse wire dependencies, retaining zero-duration measurement edges."""
    quantum = [0.0] * num_qubits
    classical = [0.0] * num_clbits
    for name, qubits, clbits, duration in nodes:
        if name == "measure" and not readout:
            duration = 0.0
        if duration is None:
            return None
        start = max([0.0, *[quantum[q] for q in qubits], *[classical[c] for c in clbits]])
        end = start + duration
        for qubit in qubits:
            quantum[qubit] = end
        for clbit in clbits:
            classical[clbit] = end
    return max([0.0, *quantum, *classical])


def timing_components(
    circuit: QuantumCircuit,
    snapshot: Mapping[str, Any],
    *,
    archived_circuit_seconds: float | None = None,
) -> dict[str, Any]:
    """Compute static wire-DAG timing on a separate circuit copy, without execution.

    Measurements retain their dependencies in both traversals. Their durations
    are zero in the native-only traversal. The readout increment is the difference
    of the two critical paths, including changes in which wire is critical; it is
    not the sum or necessarily the maximum of individual measurement durations.
    Normal initialization is a separate parallel maximum over active wires.
    """
    analysis = circuit.copy()
    durations = _duration_index(snapshot)
    missing: list[str] = []
    active: set[int] = set()
    nodes: list[tuple[str, tuple[int, ...], tuple[int, ...], float | None]] = []
    measurements, explicit_resets = 0, 0
    width = snapshot.get("backend_width")
    if width is not None and (type(width) is not int or width != analysis.num_qubits):
        missing.append("Concrete provider circuit width differs from the archived snapshot")

    def duration(name: str, qubits: tuple[int, ...]) -> float | None:
        rows = durations.get((name, qubits), [])
        if len(rows) != 1 or not _seconds(rows[0]):
            missing.append(f"Missing, ambiguous or invalid SI duration: {name}{qubits}")
            return None
        return float(rows[0])

    unsupported = bool(analysis.num_parameters)
    if unsupported:
        missing.append("Unbound native circuit parameters are unsupported")
    for item in analysis.data:
        name = item.operation.name
        qubits = tuple(analysis.find_bit(bit).index for bit in item.qubits)
        clbits = tuple(analysis.find_bit(bit).index for bit in item.clbits)
        if name == "barrier":
            nodes.append((name, qubits, clbits, 0.0))
            continue
        active.update(qubits)
        measurements += name == "measure"
        explicit_resets += name == "reset"
        if (
            name in _UNSUPPORTED
            or hasattr(item.operation, "blocks")
            or getattr(item.operation, "_condition", None) is not None
            or getattr(item.operation, "condition", None) is not None
        ):
            missing.append(f"Unsupported static timing instruction: {name}")
            unsupported = True
            nodes.append((name, qubits, clbits, None))
            continue
        nodes.append((name, qubits, clbits, duration(name, qubits)))
    reset_seconds = [duration("reset", (qubit,)) for qubit in sorted(active)]
    initialization = (
        max(float(value) for value in reset_seconds if value is not None)
        if reset_seconds and all(value is not None for value in reset_seconds)
        else None
    )
    if not active:
        missing.append("Active physical qubit inventory is empty")
    full = _critical_seconds(nodes, analysis.num_qubits, analysis.num_clbits, readout=True)
    native = _critical_seconds(nodes, analysis.num_qubits, analysis.num_clbits, readout=False)
    if unsupported or width is not None and width != analysis.num_qubits:
        full, native = None, None
    if full is not None and not _seconds(full):
        missing.append("Computed full critical duration is not finite nonnegative seconds")
        full = None
    if native is not None and not _seconds(native):
        missing.append("Computed native critical duration is not finite nonnegative seconds")
        native = None
    increment = full - native if full is not None and native is not None else None
    if increment is not None and increment < 0:
        missing.append("Negative critical-path readout increment")
        increment = None
    comparison: dict[str, Any] = {
        "status": "NOT_PROVIDED",
        "atol_seconds": ARCHIVED_DURATION_ATOL_SECONDS,
        "archived_circuit_seconds": archived_circuit_seconds,
        "absolute_difference_seconds": None,
    }
    if archived_circuit_seconds is not None:
        if not _seconds(archived_circuit_seconds):
            comparison.update(status="INVALID", archived_circuit_seconds=None)
            missing.append("Archived circuit duration must be finite nonnegative SI seconds")
        elif full is None:
            comparison["status"] = "BLOCKED"
        else:
            difference = abs(full - archived_circuit_seconds)
            matches = difference <= ARCHIVED_DURATION_ATOL_SECONDS
            comparison.update(
                status="MATCH" if matches else "MISMATCH",
                absolute_difference_seconds=difference,
            )
            if not matches:
                missing.append("Archived circuit duration differs beyond 1e-15 seconds")
    return {
        "schema_version": 1,
        "artifact_type": "m5_local_timing_components",
        "status": "BLOCKED" if missing else "COMPUTED",
        "units": "seconds",
        "full_critical_seconds": full,
        "native_without_readout_seconds": native,
        "readout_critical_increment_seconds": increment,
        "initialization_seconds": initialization,
        "measurement_count": measurements,
        "explicit_reset_count": explicit_resets,
        "active_physical_qubits": sorted(active),
        "archived_duration_comparison": comparison,
        "missing": sorted(set(missing)),
        "target_snapshot_sha256": snapshot.get("content_sha256"),
        "duration_source": "snapshot.instructions.duration_seconds",
        "analysis_method": "Static quantum/classical wire DAG; separate zero-readout traversal",
        "initialization_definition": "Parallel reset maximum over active physical qubits per shot",
        "explicit_resets_included_in_native_seconds": True,
        "full_live_target_claimed": False,
        "provider_contacted": False,
        "hardware_used": False,
        "limitations": [
            "Archived subset durations omit the complete live Target, dt and alignment context",
            "Local dependency timing is diagnostic; it does not schedule provider jobs",
        ],
    }
