"""Public Target/calibration snapshots and an explicitly synthetic offline fixture."""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal

from qiskit import QuantumCircuit, qpy
from qiskit.circuit import Measure, Parameter
from qiskit.circuit.library import CXGate, CZGate, ECRGate, RZGate, SXGate, XGate
from qiskit.providers import BackendV2, Options
from qiskit.transpiler import InstructionProperties, QubitProperties, Target

SYNTHETIC_SUBSET = (1, 4, 9, 13, 22, 31, 42)
SYNTHETIC_TIMESTAMP = "2000-01-01T00:00:00+00:00"


class AsymmetricReadoutProperties(InstructionProperties):
    """Aer BackendV2 reads these public per-direction readout attributes."""

    def __init__(self, *, duration: float, p01: float, p10: float) -> None:
        super().__init__(duration=duration, error=(p01 + p10) / 2)
        self.duration = duration
        self.error = (p01 + p10) / 2
        self.prob_meas1_prep0 = p01
        self.prob_meas0_prep1 = p10


class SyntheticBackend(BackendV2):
    """A deterministic test Target. It has no provider connection or execution path."""

    def __init__(
        self,
        *,
        num_qubits: int = 53,
        subset: Sequence[int] = SYNTHETIC_SUBSET,
        two_qubit_gate: Literal["ecr", "cz", "cx"] = "ecr",
    ) -> None:
        super().__init__(name="m5_offline_synthetic_target", backend_version="1")
        selected = tuple(subset)
        if (
            len(selected) != 7
            or len(set(selected)) != 7
            or any(type(q) is not int or not 0 <= q < num_qubits for q in selected)
        ):
            raise ValueError("Synthetic fixture needs seven distinct valid physical IDs")
        self.physical_subset = selected
        self.snapshot_source = "offline_synthetic"
        self.calibration_timestamp = SYNTHETIC_TIMESTAMP
        self._target = Target(
            description="M5 offline synthetic calibration; never IBM hardware",
            num_qubits=num_qubits,
            qubit_properties=[
                QubitProperties(t1=120e-6 + q * 1e-7, t2=90e-6 + q * 1e-7, frequency=5e9)
                for q in range(num_qubits)
            ],
        )
        for gate, duration, error in (
            (RZGate(Parameter("theta")), 0.0, 0.0),
            (SXGate(), 35e-9, 0.0004),
            (XGate(), 35e-9, 0.0005),
        ):
            self._target.add_instruction(
                gate,
                {
                    (q,): InstructionProperties(duration=duration, error=error)
                    for q in range(num_qubits)
                },
            )
        self._target.add_instruction(
            Measure(),
            {
                (q,): AsymmetricReadoutProperties(
                    duration=700e-9,
                    p01=0.01 + (q % 7) * 0.001,
                    p10=0.025 + (q % 5) * 0.001,
                )
                for q in range(num_qubits)
            },
        )
        gates = {"ecr": ECRGate, "cz": CZGate, "cx": CXGate}
        if two_qubit_gate not in gates:
            raise ValueError("Synthetic fixture supports ecr, cz, or cx")
        # Directed edges deliberately differ from logical QAOA couplings.
        edges = list(zip(selected[:-1], selected[1:], strict=True))
        self._target.add_instruction(
            gates[two_qubit_gate](),
            {
                edge: InstructionProperties(duration=280e-9, error=0.008 + index * 0.0002)
                for index, edge in enumerate(edges)
            },
        )

    @property
    def target(self) -> Target:
        return self._target

    @property
    def max_circuits(self) -> None:
        return None

    @classmethod
    def _default_options(cls) -> Options:
        return Options()

    def run(self, run_input: Any, **options: Any) -> Any:
        raise RuntimeError("Offline synthetic fixture cannot submit or execute provider jobs")


def restrict_target(target: Target, physical_subset: Sequence[int]) -> Target:
    """Constrain routing to the frozen connected area while preserving real physical IDs."""
    selected = tuple(physical_subset)
    if (
        len(selected) != 7
        or len(set(selected)) != 7
        or any(type(q) is not int or not 0 <= q < target.num_qubits for q in selected)
    ):
        raise ValueError("The initial physical subset must contain seven distinct target qubits")
    edges: set[tuple[int, int]] = set()
    for name in target.operation_names:
        for qargs in target[name]:
            if qargs is not None and len(qargs) == 2 and set(qargs).issubset(selected):
                edges.add((qargs[0], qargs[1]))
    reached = {selected[0]}
    while True:
        enlarged = reached | {
            endpoint for edge in edges if set(edge) & reached for endpoint in edge
        }
        if enlarged == reached:
            break
        reached = enlarged
    if reached != set(selected):
        raise ValueError("The frozen seven-qubit subset must be connected")
    result = Target(
        description="M5 frozen subset restriction of original Target",
        num_qubits=target.num_qubits,
        dt=target.dt,
        granularity=target.granularity,
        min_length=target.min_length,
        pulse_alignment=target.pulse_alignment,
        acquire_alignment=target.acquire_alignment,
        qubit_properties=target.qubit_properties,
    )
    for name in target.operation_names:
        operation = target.operation_from_name(name)
        if isinstance(operation, type):
            # An IBM Target may advertise control flow. M5 uses static circuits only;
            # unused variable-width features do not invalidate its static ISA.
            continue
        properties = {}
        for qargs, prop in target[name].items():
            if qargs is None:
                if operation.num_qubits != 1:
                    raise ValueError(
                        "Unrestricted multi-qubit Target instructions need explicit edges"
                    )
                for q in selected:
                    properties[(q,)] = prop
            elif set(qargs).issubset(selected):
                properties[qargs] = prop
        if properties:
            result.add_instruction(operation, properties, name=name)
    return result


def _finite_float(value: Any) -> float | None:
    import math

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def is_offline_backend(backend: Any) -> bool:
    """Recognize simulator/fake families without calling any provider service."""
    if isinstance(backend, SyntheticBackend):
        return True
    for cls in type(backend).__mro__:
        module = cls.__module__.lower()
        name = cls.__name__.lower()
        if (
            "fake_provider" in module
            or module.startswith("qiskit_aer")
            or name.startswith("fake")
            or "simulator" in name
            or name == "genericbackendv2"
        ):
            return True
    configuration = getattr(backend, "configuration", None)
    if callable(configuration):
        configured = configuration()
        if getattr(configured, "simulator", False) is True:
            return True
    return getattr(backend, "simulator", False) is True


def snapshot_backend(
    backend: Any,
    physical_subset: Sequence[int],
    *,
    retrieved_at: str | None = None,
    source: Literal["provider", "offline_synthetic", "offline_test_backend"] | None = None,
) -> dict[str, Any]:
    """Export allowlisted device facts only; no service/account/options objects are serialized."""
    target: Target = backend.target
    selected = tuple(physical_subset)
    synthetic = isinstance(backend, SyntheticBackend)
    offline = is_offline_backend(backend)
    declared_source = source or (
        "offline_synthetic" if synthetic else "offline_test_backend" if offline else "provider"
    )
    if offline and declared_source == "provider":
        raise ValueError(
            "Synthetic/fake/simulator calibration cannot be labeled as provider hardware"
        )
    if synthetic and declared_source != "offline_synthetic":
        raise ValueError("Synthetic calibration must retain its explicit offline_synthetic label")
    restrict_target(target, selected)
    instructions = []
    for name in sorted(target.operation_names):
        for qargs, prop in sorted(target[name].items(), key=lambda row: str(row[0])):
            if qargs is None or not set(qargs).issubset(selected):
                continue
            duration = _finite_float(getattr(prop, "duration", None))
            error = _finite_float(getattr(prop, "error", None))
            instructions.append(
                {
                    "name": name,
                    "qubits": list(qargs),
                    "duration_seconds": duration,
                    "error": error,
                    "missing": [
                        key
                        for key, value in (("duration_seconds", duration), ("error", error))
                        if value is None
                    ],
                }
            )
    qubits = []
    for physical in selected:
        prop = target.qubit_properties[physical] if target.qubit_properties else None
        record: dict[str, Any] = {
            "physical_id": physical,
            "t1_seconds": _finite_float(getattr(prop, "t1", None)),
            "t2_seconds": _finite_float(getattr(prop, "t2", None)),
            "frequency_hz": _finite_float(getattr(prop, "frequency", None)),
            "readout_error": None,
            "readout_matrix": None,
            "readout_matrix_source": None,
        }
        measure = target["measure"].get((physical,)) if "measure" in target else None
        record["readout_error"] = _finite_float(getattr(measure, "error", None))
        p01 = _finite_float(getattr(measure, "prob_meas1_prep0", None))
        p10 = _finite_float(getattr(measure, "prob_meas0_prep1", None))
        if p01 is not None and p10 is not None:
            record["readout_matrix"] = [[1 - p01, p01], [p10, 1 - p10]]
            record["readout_matrix_source"] = "target_asymmetric_probabilities"
        elif record["readout_error"] is not None:
            error = record["readout_error"]
            record["readout_matrix"] = [[1 - error, error], [error, 1 - error]]
            record["readout_matrix_source"] = "symmetric_approximation_from_target_mean_error"
        record["missing"] = [
            key
            for key in (
                "t1_seconds",
                "t2_seconds",
                "frequency_hz",
                "readout_error",
                "readout_matrix",
            )
            if record[key] is None
        ]
        qubits.append(record)
    calibration = getattr(backend, "calibration_timestamp", None)
    if isinstance(calibration, datetime):
        calibration = calibration.isoformat()
    if not isinstance(calibration, str):
        calibration = None
    constraints = target.timing_constraints()
    target_timing = {
        "dt_seconds": _finite_float(target.dt),
        "timing_constraints": {
            key: value if type(value) is int and value > 0 else None
            for key in ("granularity", "min_length", "pulse_alignment", "acquire_alignment")
            for value in (getattr(constraints, key, None),)
        },
        "source": "qiskit.transpiler.Target.dt/timing_constraints()",
    }
    result = {
        "schema_version": 2,
        "artifact_type": "m5_backend_snapshot",
        "source": declared_source,
        "hardware_used": False,
        "synthetic_model_is_device_calibration": False if offline else None,
        "backend_name": str(backend.name),
        "backend_width": target.num_qubits,
        "physical_subset": list(selected),
        "operation_names": sorted(target.operation_names),
        "target_timing": target_timing,
        "instructions": instructions,
        "qubits": qubits,
        "retrieved_at": retrieved_at or datetime.now(UTC).isoformat(),
        "calibration_timestamp": calibration,
        "missing": [] if calibration else ["calibration_timestamp"],
        "timestamp_definition": (
            "Fixed test-only fixture epoch, not a real calibration time"
            if synthetic
            else "Local retrieval time; calibration time missing unless explicitly available"
        ),
        "readout_matrix_convention": "rows=true, columns=observed",
        "credentials_exported": False,
    }
    json.dumps(result, allow_nan=False)
    return result


def qpy_bytes(circuit: QuantumCircuit) -> bytes:
    stream = io.BytesIO()
    qpy.dump(circuit, stream)
    return stream.getvalue()


def load_qpy_bytes(payload: bytes, *, expected_sha256: str | None = None) -> QuantumCircuit:
    if expected_sha256 and hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("QPY raw byte SHA256 mismatch")
    circuits = qpy.load(io.BytesIO(payload))
    if len(circuits) != 1:
        raise ValueError("An M5 QPY artifact must contain exactly one circuit")
    return circuits[0]


def write_qpy(path: str | Path, circuit: QuantumCircuit) -> dict[str, Any]:
    """Retain existing raw bytes; QPY serialization is not a canonical circuit hash."""
    destination = Path(path)
    if destination.exists():
        payload = destination.read_bytes()
        existing = load_qpy_bytes(payload)
        if not _same_qpy_circuit(existing, circuit):
            raise ValueError("Existing immutable QPY artifact differs")
    else:
        payload = qpy_bytes(circuit)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    loaded = load_qpy_bytes(payload, expected_sha256=digest)
    if not _same_qpy_circuit(loaded, circuit):
        raise ValueError("QPY round-trip changed circuit structure or parameters")
    return {
        "path": str(destination),
        "sha256": digest,
        "byte_count": len(payload),
        "qiskit_version": version("qiskit"),
        "qpy_version": qpy.common.QPY_VERSION,
        "round_trip_structure_verified": True,
        "version_independent_format": False,
        "serialization_is_canonical": False,
    }


def _same_qpy_circuit(loaded: QuantumCircuit, original: QuantumCircuit) -> bool:
    if loaded != original or loaded.name != original.name or loaded.metadata != original.metadata:
        return False
    if (loaded.layout is None) != (original.layout is None):
        return False
    if loaded.layout is not None:
        return (
            loaded.layout.initial_index_layout() == original.layout.initial_index_layout()
            and loaded.layout.final_index_layout() == original.layout.final_index_layout()
        )
    return True


def load_qpy(path: str | Path, *, expected_sha256: str | None = None) -> QuantumCircuit:
    return load_qpy_bytes(Path(path).read_bytes(), expected_sha256=expected_sha256)
