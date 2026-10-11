"""Frozen compilation, exact synthetic channels, and separate classical readout.

No measurements or readout errors enter the density-matrix simulator.  Its Born
distribution is transformed once by row-stochastic true-to-observed matrices;
only afterward is a finite multinomial sample drawn.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from time import perf_counter
from typing import Any

import numpy as np
import psutil
from numpy.typing import NDArray
from qiskit import QuantumCircuit, transpile
from qiskit.quantum_info import Statevector
from qiskit_aer import AerSimulator
from qiskit_aer.library import SaveDensityMatrix
from qiskit_aer.noise import NoiseModel, depolarizing_error
from scipy.linalg import eigvalsh
from threadpoolctl import threadpool_limits

FloatArray = NDArray[np.float64]
ComplexArray = NDArray[np.complex128]
PHYSICALITY_ATOL = 1e-9
COMPILATION_ATOL = 1e-9
COMPILATION_RTOL = 1e-12
M4_BASIS_GATES = ("rz", "sx", "x", "cx")


@dataclass(frozen=True)
class CompilerConfig:
    seed: int = 424242
    optimization_level: int = 0
    basis_gates: tuple[str, ...] = M4_BASIS_GATES

    def __post_init__(self) -> None:
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError("Compiler seed must be a nonnegative integer")
        if self.optimization_level != 0 or type(self.optimization_level) is not int:
            raise ValueError("M4 freezes optimization_level=0")
        if self.basis_gates != M4_BASIS_GATES:
            raise ValueError("M4 freezes basis_gates=rz/sx/x/cx")


@dataclass(frozen=True)
class ResourceLimits:
    max_qubits: int = 10
    max_memory_mb: int = 512
    max_parallel_threads: int = 2
    max_instances: int = 12
    max_training_runs: int = 240
    max_density_runs: int = 1200

    def __post_init__(self) -> None:
        for name, minimum, maximum in (
            ("max_qubits", 0, 10),
            ("max_memory_mb", 1, 512),
            ("max_parallel_threads", 1, 2),
            ("max_instances", 1, 12),
            ("max_training_runs", 1, 240),
            ("max_density_runs", 1, 1200),
        ):
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be an integer in [{minimum},{maximum}]")


@dataclass(frozen=True)
class NoiseProfile:
    name: str
    lambda_1q: float
    lambda_2q: float
    readout_flip: float

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("A noise profile needs a name")
        for name in ("lambda_1q", "lambda_2q", "readout_flip"):
            value = getattr(self, name)
            # The artificial M4 profiles use convex mixtures, lambda in [0,1].
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float, np.integer, np.floating))
                or not np.isfinite(value)
                or not 0 <= value <= 1
            ):
                raise ValueError(f"{name} must be finite and in [0,1]")


DEFAULT_NOISE_PROFILES = (
    NoiseProfile("ideal", 0.0, 0.0, 0.0),
    NoiseProfile("gate_only", 0.0005, 0.005, 0.0),
    NoiseProfile("readout_only", 0.0, 0.0, 0.01),
    NoiseProfile("combined_low", 0.0005, 0.005, 0.01),
    NoiseProfile("combined_high", 0.002, 0.02, 0.03),
)


@dataclass(frozen=True)
class CompiledCircuit:
    circuit: QuantumCircuit
    ideal_probabilities: FloatArray
    metadata: dict[str, Any]
    sequence_hash: str


@dataclass(frozen=True)
class DensityResult:
    probabilities_before_readout: FloatArray
    physicality: dict[str, Any]
    coverage: dict[str, Any]
    runtime_ms: float
    backend_metadata: dict[str, Any]


def resource_preflight(
    number_qubits: int,
    limits: ResourceLimits | None = None,
    *,
    instance_count: int = 0,
    training_runs: int = 0,
    density_runs: int = 0,
) -> dict[str, Any]:
    """Check the frozen caps and conservative eight-matrix workspace estimate.

    Aer limits its own state storage; the separate workspace bound also covers
    density copies and physicality eigensolver temporaries.  It is a budgeting
    estimate, not a measured peak-process resident-memory claim.
    """
    limits = limits or ResourceLimits()
    if type(number_qubits) is not int or not 0 <= number_qubits <= limits.max_qubits:
        raise ValueError("The circuit exceeds the frozen max_qubits limit")
    for name, value, maximum in (
        ("instance_count", instance_count, limits.max_instances),
        ("training_runs", training_runs, limits.max_training_runs),
        ("density_runs", density_runs, limits.max_density_runs),
    ):
        if type(value) is not int or not 0 <= value <= maximum:
            raise ValueError(f"{name} exceeds its frozen run limit")
    memory = psutil.virtual_memory()
    state_bytes = 16 * 4**number_qubits
    workspace_bytes = 8 * state_bytes
    memory_limit = limits.max_memory_mb * 1024**2
    if workspace_bytes > memory_limit:
        raise ValueError("Density workspace estimate exceeds max_memory_mb")
    if workspace_bytes > memory.available:
        raise ValueError("Insufficient available RAM for the density workspace estimate")
    logical_cpus = psutil.cpu_count(logical=True) or 1
    if limits.max_parallel_threads > logical_cpus:
        raise ValueError("Configured thread limit exceeds available logical CPUs")
    return {
        "passed": True,
        "number_qubits": number_qubits,
        "density_complex_entries": 4**number_qubits,
        "density_state_bytes": state_bytes,
        "estimated_workspace_bytes": workspace_bytes,
        "workspace_matrix_multiplier": 8,
        "physical_ram_bytes": int(memory.total),
        "available_ram_bytes": int(memory.available),
        "logical_cpu_count": logical_cpus,
        "limits": asdict(limits),
        "instance_count": instance_count,
        "training_runs": training_runs,
        "density_runs": density_runs,
        "parallel_experiments": 1,
        "parallel_shots": 1,
        "parallel_run_workers": 1,
        "blas_thread_limit": limits.max_parallel_threads,
    }


def _bound_unitary(circuit: QuantumCircuit) -> None:
    if circuit.parameters:
        raise ValueError("Compilation requires a fully bound circuit")
    if circuit.num_clbits:
        raise ValueError("The circuit must be unmeasured and have no classical bits")
    if any(
        item.operation.name in {"measure", "reset", "initialize", "save_density_matrix"}
        for item in circuit.data
    ):
        raise ValueError("Only the unmeasured unitary circuit may be compiled")


def circuit_payload(circuit: QuantumCircuit) -> dict[str, Any]:
    """Canonical bound gate sequence, including global phase and qubit indices."""
    _bound_unitary(circuit)
    operations: list[dict[str, Any]] = []
    for item in circuit.data:
        if item.operation.name not in M4_BASIS_GATES:
            raise ValueError("Compiled circuit contains an instruction outside the frozen basis")
        parameters = [float(value) for value in item.operation.params]
        if not np.all(np.isfinite(parameters)):
            raise ValueError("Compiled gate parameters must be finite")
        operations.append(
            {
                "name": item.operation.name,
                "qubits": [circuit.find_bit(qubit).index for qubit in item.qubits],
                "parameters": parameters,
            }
        )
    phase = float(circuit.global_phase)
    if not np.isfinite(phase):
        raise ValueError("Compiled global phase must be finite")
    return {
        "schema_version": 1,
        "number_qubits": circuit.num_qubits,
        "global_phase": phase,
        "operations": operations,
    }


def circuit_sequence_hash(circuit: QuantumCircuit) -> str:
    payload = json.dumps(circuit_payload(circuit), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compile_bound_circuit(
    circuit: QuantumCircuit,
    config: CompilerConfig | None = None,
    limits: ResourceLimits | None = None,
) -> CompiledCircuit:
    """Compile once without a backend topology and verify all amplitudes."""
    started = perf_counter()
    config = config or CompilerConfig()
    limits = limits or ResourceLimits()
    _bound_unitary(circuit)
    resource_preflight(circuit.num_qubits, limits)
    with threadpool_limits(limits=limits.max_parallel_threads):
        original = np.asarray(Statevector.from_instruction(circuit).data, dtype=complex)
        compiled = transpile(
            circuit,
            basis_gates=list(config.basis_gates),
            coupling_map=None,
            initial_layout=None,
            seed_transpiler=config.seed,
            optimization_level=config.optimization_level,
            num_processes=1,
        )
        if compiled.num_qubits != circuit.num_qubits or compiled.layout is not None:
            raise ValueError("Compilation unexpectedly changed the qubit layout")
        transformed = np.asarray(Statevector.from_instruction(compiled).data, dtype=complex)
    anchor = int(np.argmax(np.abs(original)))
    phase = transformed[anchor] / original[anchor]
    phase /= abs(phase)
    discrepancy = float(np.max(np.abs(transformed - phase * original)))
    if not np.allclose(transformed, phase * original, atol=COMPILATION_ATOL, rtol=COMPILATION_RTOL):
        raise ValueError("Ideal pre/post compilation statevectors are not equivalent")
    probabilities = np.asarray(np.abs(original) ** 2, dtype=float)
    probabilities.setflags(write=False)
    payload = circuit_payload(compiled)
    histogram = {str(name): int(count) for name, count in sorted(compiled.count_ops().items())}
    active_qubits = sorted({index for op in payload["operations"] for index in op["qubits"]})
    metadata = {
        "compiler": asdict(config),
        "coupling_map": None,
        "layout": None,
        "routing": False,
        "hardware_target": False,
        "number_qubits": compiled.num_qubits,
        "active_qubits": active_qubits,
        "active_qubit_count": len(active_qubits),
        "gate_histogram": histogram,
        "gate_count": len(compiled.data),
        "one_qubit_gate_count": sum(len(item.qubits) == 1 for item in compiled.data),
        "two_qubit_gate_count": sum(len(item.qubits) == 2 for item in compiled.data),
        "depth": int(compiled.depth()),
        "ideal_equivalence_verified": True,
        "max_amplitude_difference_up_to_global_phase": discrepancy,
        "equivalence_atol": COMPILATION_ATOL,
        "equivalence_rtol": COMPILATION_RTOL,
        "includes_state_preparation_and_mixer": True,
        "gate_sequence": payload,
        "compile_runtime_ms": (perf_counter() - started) * 1000,
    }
    sequence_hash = circuit_sequence_hash(compiled)
    metadata["sequence_sha256"] = sequence_hash
    metadata = json.loads(json.dumps(metadata, allow_nan=False))
    return CompiledCircuit(compiled, probabilities, metadata, sequence_hash)


def gate_noise_model(profile: NoiseProfile) -> NoiseModel:
    """Synthetic channels after sx/x and cx; rz and readout stay ideal here."""
    model = NoiseModel(basis_gates=list(M4_BASIS_GATES))
    if profile.lambda_1q:
        model.add_all_qubit_quantum_error(depolarizing_error(profile.lambda_1q, 1), ["sx", "x"])
    if profile.lambda_2q:
        model.add_all_qubit_quantum_error(depolarizing_error(profile.lambda_2q, 2), ["cx"])
    return model


def noise_gate_coverage(
    circuit: QuantumCircuit, profile: NoiseProfile, model: NoiseModel
) -> dict[str, Any]:
    """Reject a model whose instruction names miss gates actually exposed."""
    histogram = dict(circuit.count_ops())
    expected = {
        name
        for name, strength in (
            ("sx", profile.lambda_1q),
            ("x", profile.lambda_1q),
            ("cx", profile.lambda_2q),
        )
        if strength and histogram.get(name, 0)
    }
    actual = set(model.noise_instructions)
    if not expected <= actual:
        raise ValueError("Noise model does not cover the active compiled gates")
    if "measure" in actual:
        raise ValueError("Readout must not be attached to the pre-measurement density model")
    one_count = int(histogram.get("sx", 0) + histogram.get("x", 0))
    two_count = int(histogram.get("cx", 0))
    return {
        "verified": True,
        "model_noise_instructions": sorted(actual),
        "active_noisy_instruction_names": sorted(expected),
        "eligible_one_qubit_gate_count": one_count,
        "eligible_two_qubit_gate_count": two_count,
        "one_qubit_channel_applications": one_count if profile.lambda_1q else 0,
        "two_qubit_channel_applications": two_count if profile.lambda_2q else 0,
        "total_channel_applications": (one_count if profile.lambda_1q else 0)
        + (two_count if profile.lambda_2q else 0),
        "rz_is_ideal": True,
        "readout_in_density_model": False,
        "state_preparation_included": True,
        "missing_two_qubit_exposure_is_legitimate": two_count == 0,
    }


def check_density_matrix(
    density: ComplexArray,
    number_qubits: int,
    *,
    max_parallel_threads: int = 2,
) -> tuple[FloatArray, dict[str, Any]]:
    """Test trace, Hermiticity and PSD before extracting Born probabilities."""
    if type(number_qubits) is not int or not 0 <= number_qubits <= 10:
        raise ValueError("Density physicality checking supports at most ten qubits")
    if type(max_parallel_threads) is not int or not 1 <= max_parallel_threads <= 2:
        raise ValueError("BLAS thread limit must be one or two")
    matrix = np.asarray(density, dtype=complex)
    dimension = 2**number_qubits
    if matrix.shape != (dimension, dimension) or not np.all(np.isfinite(matrix)):
        raise ValueError("Density matrix has an invalid dimension or nonfinite entries")
    trace = complex(np.trace(matrix))
    hermiticity_error = float(np.max(np.abs(matrix - matrix.conj().T)))
    if abs(trace - 1) > PHYSICALITY_ATOL:
        raise ValueError("Density matrix trace is not one")
    if hermiticity_error > PHYSICALITY_ATOL:
        raise ValueError("Density matrix is not Hermitian")
    with threadpool_limits(limits=max_parallel_threads):
        # Symmetrization affects the check only, never the simulated density.
        minimum_eigenvalue = float(
            eigvalsh((matrix + matrix.conj().T) / 2, subset_by_index=[0, 0])[0]
        )
    if minimum_eigenvalue < -PHYSICALITY_ATOL:
        raise ValueError("Density matrix is not positive semidefinite")
    diagonal = np.asarray(np.real(np.diag(matrix)), dtype=float)
    if np.min(diagonal) < -PHYSICALITY_ATOL:
        raise ValueError("Born probabilities contain a negative entry")
    probabilities = np.maximum(diagonal, 0.0)
    probabilities /= probabilities.sum()
    repair = float(np.max(np.abs(probabilities - diagonal)))
    probabilities.setflags(write=False)
    return probabilities, {
        "verified": True,
        "dimension": dimension,
        "trace_real": trace.real,
        "trace_imaginary": trace.imag,
        "trace_absolute_error": abs(trace - 1),
        "hermiticity_max_absolute_error": hermiticity_error,
        "minimum_eigenvalue": minimum_eigenvalue,
        "positivity_atol": PHYSICALITY_ATOL,
        "normalization_atol": PHYSICALITY_ATOL,
        "born_probability_max_roundoff_adjustment": repair,
        "readout_applied": False,
    }


def simulate_density(
    compiled: CompiledCircuit,
    profile: NoiseProfile,
    limits: ResourceLimits | None = None,
) -> DensityResult:
    """Exact channel evolution with a full density save before measurement."""
    started = perf_counter()
    limits = limits or ResourceLimits()
    resource_preflight(compiled.circuit.num_qubits, limits)
    if circuit_sequence_hash(compiled.circuit) != compiled.sequence_hash:
        raise ValueError("The frozen compiled circuit was mutated between noise profiles")
    model = gate_noise_model(profile)
    coverage = noise_gate_coverage(compiled.circuit, profile, model)
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
        # Keep the specified noisy gate applications visible to Aer.
        "fusion_enable": False,
    }
    with threadpool_limits(limits=limits.max_parallel_threads):
        if compiled.circuit.num_qubits:
            saved = compiled.circuit.copy()
            saved.append(SaveDensityMatrix(saved.num_qubits, label="rho"), saved.qubits)
            backend = AerSimulator(noise_model=model, **options)
            result = backend.run(saved, shots=1, seed_simulator=0).result()
            if not result.success or not result.results[0].success:
                raise ValueError(f"Density simulator failed: {result.status}")
            density = np.asarray(result.data(0)["rho"].data, dtype=complex)
            simulator_metadata = result.results[0].metadata
        else:
            density = np.ones((1, 1), dtype=complex)
            simulator_metadata = {"method": "analytical_zero_qubit"}
        probabilities, physicality = check_density_matrix(
            density,
            compiled.circuit.num_qubits,
            max_parallel_threads=limits.max_parallel_threads,
        )
    if not profile.lambda_1q and not profile.lambda_2q:
        if not np.allclose(
            probabilities,
            compiled.ideal_probabilities,
            atol=COMPILATION_ATOL,
            rtol=COMPILATION_RTOL,
        ):
            raise ValueError("Zero-noise density disagrees with the original ideal statevector")
        with threadpool_limits(limits=limits.max_parallel_threads):
            state = np.asarray(Statevector.from_instruction(compiled.circuit).data, dtype=complex)
            pure_density = np.outer(state, state.conj())
            density_discrepancy = float(np.max(np.abs(density - pure_density)))
            if not np.allclose(density, pure_density, atol=COMPILATION_ATOL, rtol=COMPILATION_RTOL):
                raise ValueError(
                    "Zero-noise density coherences disagree with the ideal statevector"
                )
        physicality["zero_noise_statevector_equivalence_verified"] = True
        physicality["zero_noise_density_max_absolute_difference"] = density_discrepancy
    else:
        physicality["zero_noise_statevector_equivalence_verified"] = None
    return DensityResult(
        probabilities,
        physicality,
        coverage,
        (perf_counter() - started) * 1000,
        {
            "options": options,
            "seed_simulator": 0,
            "snapshot_execution_shots": 1,
            "shots_are_not_a_density_estimation_budget": True,
            "measurement_present": False,
            "readout_present": False,
            "sequence_sha256": compiled.sequence_hash,
            "aer": simulator_metadata,
        },
    )


def _probability_vector(probabilities: Sequence[float] | FloatArray) -> FloatArray:
    vector = np.asarray(probabilities, dtype=float)
    if vector.ndim != 1 or not len(vector) or len(vector) & (len(vector) - 1):
        raise ValueError("Probability vector dimension must be a positive power of two")
    if not np.all(np.isfinite(vector)) or np.any(vector < 0):
        raise ValueError("Probabilities must be finite and nonnegative")
    if not np.isclose(vector.sum(), 1.0, atol=PHYSICALITY_ATOL, rtol=0):
        raise ValueError("Probabilities must sum to one")
    return vector / vector.sum()


def symmetric_readout_matrices(number_qubits: int, flip: float) -> tuple[FloatArray, ...]:
    if type(number_qubits) is not int or not 0 <= number_qubits <= 10:
        raise ValueError("Readout supports at most ten qubits")
    if isinstance(flip, bool) or not np.isfinite(flip) or not 0 <= flip <= 1:
        raise ValueError("Readout flip must be finite and in [0,1]")
    return tuple(np.array([[1 - flip, flip], [flip, 1 - flip]]) for _ in range(number_qubits))


def apply_readout(
    probabilities: Sequence[float] | FloatArray,
    matrices: Sequence[Sequence[Sequence[float]] | FloatArray],
) -> FloatArray:
    """Apply independent row=true/column=observed matrices once in qubit order.

    Flat vector index has qubit i at binary weight 2**i.  Reshape axes therefore
    run from qubit n-1 down to qubit 0; using R.T is essential for asymmetry.
    """
    vector = _probability_vector(probabilities)
    number_qubits = len(vector).bit_length() - 1
    if len(matrices) != number_qubits:
        raise ValueError("One readout matrix is required per qubit")
    tensor = vector.reshape((2,) * number_qubits) if number_qubits else vector
    for qubit, values in enumerate(matrices):
        matrix = np.asarray(values, dtype=float)
        if (
            matrix.shape != (2, 2)
            or not np.all(np.isfinite(matrix))
            or np.any(matrix < 0)
            or not np.allclose(matrix.sum(axis=1), 1, atol=PHYSICALITY_ATOL, rtol=0)
        ):
            raise ValueError("Readout matrices must be finite nonnegative row-stochastic 2x2")
        axis = number_qubits - 1 - qubit
        transformed = np.tensordot(matrix.T, tensor, axes=([1], [axis]))
        tensor = np.moveaxis(transformed, 0, axis)
    observed = np.asarray(tensor.reshape(-1), dtype=float)
    observed /= observed.sum()
    observed.setflags(write=False)
    return observed


def evaluate_noise(
    compiled: CompiledCircuit,
    profile: NoiseProfile,
    limits: ResourceLimits | None = None,
) -> tuple[FloatArray, FloatArray, dict[str, Any]]:
    """Return before/after-readout exact distributions and their provenance."""
    result = simulate_density(compiled, profile, limits)
    started = perf_counter()
    matrices = symmetric_readout_matrices(compiled.circuit.num_qubits, profile.readout_flip)
    observed = apply_readout(result.probabilities_before_readout, matrices)
    readout_ms = (perf_counter() - started) * 1000
    return (
        result.probabilities_before_readout,
        observed,
        {
            "noise_profile": asdict(profile),
            "density_physicality": result.physicality,
            "noise_gate_coverage": result.coverage,
            "density_backend": result.backend_metadata,
            "density_runtime_ms": result.runtime_ms,
            "readout_runtime_ms": readout_ms,
            "readout_matrix_convention": "rows=true, columns=observed; p_observed=R.T@p_true",
            "readout_matrices_qubit_order": [matrix.tolist() for matrix in matrices],
            "readout_applications": 1,
            "total_variation_distance_ideal_to_before_readout": float(
                0.5
                * np.abs(compiled.ideal_probabilities - result.probabilities_before_readout).sum()
            ),
            "total_variation_distance_ideal_to_observed": float(
                0.5 * np.abs(compiled.ideal_probabilities - observed).sum()
            ),
            "hardware_used": False,
            "synthetic_model_is_device_calibration": False,
        },
    )


def multinomial_counts(
    probabilities: Sequence[float] | FloatArray,
    *,
    shots: int = 1024,
    seed: int,
) -> dict[str, int]:
    """Finite classical sample of a declared distribution, in Qiskit display order."""
    if type(shots) is not int or shots < 1 or type(seed) is not int or seed < 0:
        raise ValueError("shots must be a positive and seed a nonnegative integer")
    vector = _probability_vector(probabilities)
    number_qubits = len(vector).bit_length() - 1
    draws = np.random.default_rng(seed).multinomial(shots, vector)
    return {
        format(index, f"0{number_qubits}b") if number_qubits else "": int(count)
        for index, count in enumerate(draws)
        if count
    }
