"""Offline preparation, immutable circuit inventory and bounded local verification."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from km_quantum.m4.io import lf_file_hash
from km_quantum.m4.metrics import analyze_distribution, build_plan_table
from km_quantum.m4.noise import ResourceLimits
from km_quantum.m5.approvals import validate_plan
from km_quantum.m5.circuits import (
    FrozenCircuit,
    HardwareCompilation,
    compile_hardware,
    compiled_ideal_probabilities,
    logical_probabilities,
    reconstruct_suite,
    smoke_circuits,
    verify_compilation,
)
from km_quantum.m5.device_noise import build_device_noise_prediction
from km_quantum.m5.io import (
    historical_integrity,
    output_directory,
    raw_hash,
    read_sealed,
    sealed_json,
    utc_now,
)
from km_quantum.m5.manifest import git_context, selection_manifest, source_context
from km_quantum.m5.mapping import MeasurementMapping
from km_quantum.m5.models import M5Config
from km_quantum.m5.snapshots import (
    SYNTHETIC_SUBSET,
    SyntheticBackend,
    load_qpy,
    snapshot_backend,
    write_qpy,
)


def artifact_path(root: Path, relative: str, output: Path) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(output.resolve()):
        raise ValueError("Artifact path escaped its M5 output directory")
    return path


def restore_compilation(root: Path, output: Path, record: dict[str, Any]) -> HardwareCompilation:
    mapping = record["mapping"]
    restored = MeasurementMapping(
        m2_to_logical=tuple(mapping["m2_to_logical"]),
        logical_to_initial_physical=tuple(mapping["logical_to_initial_physical"]),
        m2_to_final_physical=tuple(mapping["m2_to_final_physical"]),
        m2_to_classical=tuple(mapping["m2_to_classical"]),
        active_physical_qubits=tuple(mapping["active_physical_qubits"]),
        provider_width=mapping["provider_width"],
        register_name=mapping["register_name"],
    )
    loaded = {
        key: load_qpy(artifact_path(root, meta["path"], output), expected_sha256=meta["sha256"])
        for key, meta in record["qpy"].items()
    }
    return HardwareCompilation(
        loaded["provider"],
        loaded["compact"],
        restored,
        restored.active_physical_qubits,
        record["summary"],
    )


def load_prepared(root: Path, output: Path) -> dict[str, Any]:
    prepared = read_sealed(output / "prepared_manifest.json")
    if prepared["source_context_sha256"] != source_context(root)["content_sha256"]:
        raise ValueError("M5 implementation changed after preparation; use a new output directory")
    selection = read_prepared_selection(root, output, prepared)
    if prepared["selection_manifest_sha256"] != selection["content_sha256"]:
        raise ValueError("Frozen selection manifest changed")
    if selection["protocol_lf_sha256"] != lf_file_hash(root / "docs/m5_experiment_protocol.md"):
        raise ValueError("Precommitted protocol changed after preparation")
    if selection["work_order_sha256"] != raw_hash(
        root / "KM3_M5_IBM_Hardware_Validation_Prompt.md"
    ):
        raise ValueError("M5 work order changed")
    snapshot = read_sealed(output / "target_snapshot.json")
    if snapshot["content_sha256"] != prepared["target_snapshot_sha256"]:
        raise ValueError("Target snapshot changed")
    if historical_integrity(root)["status"] != "PASS":
        raise ValueError("Protected M1–M4 artifacts changed")
    return prepared


def read_prepared_selection(root: Path, output: Path, prepared: dict[str, Any]) -> dict[str, Any]:
    """Version B has an output-local selection; historical V1 keeps its original file."""
    relative = prepared.get("selection_manifest_path")
    path = (
        artifact_path(root, relative, output)
        if relative is not None
        else root / "data/synthetic/m5/selection_manifest.json"
    )
    selection = read_sealed(path)
    config = M5Config.model_validate(
        selection["config"] if relative is not None else selection.get("config", {})
    )
    if (relative is not None) != (config.budget_authorization is not None):
        raise ValueError("Selection path differs from the versioned budget policy")
    if config.budget_authorization is not None and (
        prepared.get("budget_policy_version") != config.budget_policy_version
        or prepared.get("budget_authorization_sha256")
        != config.resolved_budget_policy().budget_authorization_sha256
    ):
        raise ValueError("Prepared inventory differs from its budget authorization")
    return selection


def load_prepared_config(root: Path, output: Path) -> M5Config:
    prepared = load_prepared(root, output)
    return M5Config.model_validate(read_prepared_selection(root, output, prepared)["config"])


def verify_plan_inventory_binding(plan: dict[str, Any], prepared: dict[str, Any]) -> None:
    """Bind every execution artifact to the verified inventory, even during collection."""
    validate_plan(plan, require_hardware=plan.get("provider_verified") is True)
    for field, inventory_field in (
        ("prepared_manifest_sha256", "content_sha256"),
        ("selection_manifest_sha256", "selection_manifest_sha256"),
        ("source_context_sha256", "source_context_sha256"),
        ("target_snapshot_sha256", "target_snapshot_sha256"),
    ):
        if plan.get(field) != prepared[inventory_field]:
            raise ValueError("Execution plan differs from the frozen inventory: " + field)
    if plan["layout"] != prepared["physical_subset"]:
        raise ValueError("Execution plan physical layout changed")
    for field in ("budget_policy_version", "budget_authorization_sha256"):
        value = prepared.get(field)
        plan_value = (
            plan.get("budget", {}).get(field)
            if field == "budget_authorization_sha256"
            else plan.get(field)
        )
        if value != plan_value:
            raise ValueError("Execution plan budget differs from the frozen inventory: " + field)
    records = {row["circuit_id"]: row for row in prepared["records"]}
    if set(plan["circuits"]) != set(records):
        raise ValueError("Execution circuit inventory changed")
    for identity, metadata in plan["circuits"].items():
        record = records[identity]
        if (
            metadata["qpy_sha256"] != record["qpy"]["provider"]["sha256"]
            or metadata["path"] != record["qpy"]["provider"]["path"]
            or metadata["mapping"] != record["mapping"]
            or metadata["num_bits"] != record["num_bits"]
        ):
            raise ValueError("Execution QPY bytes, path or bit mapping changed")
    for job in plan["jobs"]:
        kind = "smoke" if job["kind"] == "smoke" else "main"
        wanted = {key for key, row in records.items() if row["kind"] == kind}
        if {pub["circuit_id"] for pub in job["pubs"]} != wanted:
            raise ValueError("Execution job replaced a required circuit")
        for pub in job["pubs"]:
            if pub["case_name"] != records[pub["circuit_id"]]["reference_name"]:
                raise ValueError("Execution PUB case identity changed")


def prepare(
    root: Path,
    output: str | Path,
    config: M5Config | None = None,
    *,
    backend: Any = None,
    physical_subset: tuple[int, ...] | None = None,
    calibration_timestamp: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    destination = output_directory(root, output)
    config = config or M5Config()
    if (destination / "prepared_manifest.json").exists():
        if load_prepared_config(root, destination) != config:
            raise ValueError("Prepared budget differs; use a new output directory")
        checked = verify_local(root, destination)
        inventory = load_prepared(root, destination)
        blocked = any(row["device_noise_status"] == "BLOCKED" for row in inventory["records"])
        return {
            "status": "BLOCKED"
            if blocked
            else "OFFLINE_READY"
            if checked["source_kind"] != "provider"
            else "LOCAL_READY",
            "resumed": True,
            "hardware_used": False,
            "prepared_manifest_sha256": load_prepared(root, destination)["content_sha256"],
        }
    integrity = historical_integrity(root)
    if integrity["status"] != "PASS":
        raise ValueError("Historical M1–M4 integrity gate failed")
    device = backend if backend is not None else SyntheticBackend()
    subset = physical_subset if physical_subset is not None else SYNTHETIC_SUBSET
    suite = reconstruct_suite(root)
    selection_path = (
        destination / "selection_manifest.json"
        if config.budget_authorization is not None
        else root / "data/synthetic/m5/selection_manifest.json"
    )
    selection = sealed_json(selection_path, selection_manifest(root, suite, config))
    context = source_context(root)
    draft_path = destination / "preparation_context.json"
    if draft_path.exists():
        draft = read_sealed(draft_path)
        if (
            draft["source_context"] != context
            or draft["selection_manifest_sha256"] != selection["content_sha256"]
        ):
            raise ValueError("Interrupted preparation has different inputs")
    else:
        draft = sealed_json(
            draft_path,
            {
                "artifact_type": "m5_preparation_context",
                "created_at": utc_now(),
                "source_context": context,
                "git": git_context(root),
                "selection_manifest_sha256": selection["content_sha256"],
            },
        )
    snapshot_path = destination / "target_snapshot.json"
    if snapshot_path.exists():
        snapshot = read_sealed(snapshot_path)
        if snapshot["backend_name"] != device.name or snapshot["physical_subset"] != list(subset):
            raise ValueError("Interrupted preparation backend or subset changed")
    else:
        snapshot_data = snapshot_backend(device, subset)
        if calibration_timestamp:
            snapshot_data["calibration_timestamp"] = calibration_timestamp
            snapshot_data["missing"] = [
                field for field in snapshot_data["missing"] if field != "calibration_timestamp"
            ]
            snapshot_data["timestamp_definition"] = (
                "Read-only backend.properties().last_update_date"
            )
        snapshot = sealed_json(snapshot_path, snapshot_data)
    records: list[dict[str, Any]] = []
    logical_items: list[tuple[str, Any, FrozenCircuit | None]] = [
        (item.circuit_id, item.circuit, item) for item in suite
    ]
    logical_items.extend((circuit.name, circuit, None) for circuit in smoke_circuits())
    for circuit_id, logical, frozen in logical_items:
        record_path = destination / "circuits" / circuit_id / "record.json"
        if record_path.exists():
            records.append(read_sealed(record_path))
            continue
        compiled = compile_hardware(
            logical, device.target, subset, max_active_qubits=config.max_active_qubits
        )
        metadata = {}
        for kind, circuit in (
            ("logical", logical),
            ("provider", compiled.provider_circuit),
            ("compact", compiled.compact_circuit),
        ):
            path = destination / "circuits" / circuit_id / f"{kind}.qpy"
            metadata[kind] = {**write_qpy(path, circuit), "path": path.relative_to(root).as_posix()}
        ideal = logical_probabilities(logical)
        compiled_ideal = compiled_ideal_probabilities(compiled)
        if not np.allclose(ideal, compiled_ideal, rtol=0, atol=config.equivalence_atol):
            raise ValueError("Smoke or main compilation changed ideal distribution")
        equivalence = (
            verify_compilation(frozen, compiled)
            if frozen
            else {
                "passed": True,
                "maximum_probability_absolute_difference": float(
                    np.max(np.abs(ideal - compiled_ideal))
                ),
                "expected_m2_bits": logical.metadata["expected_m2_bits"],
            }
        )
        noise = build_device_noise_prediction(
            device,
            compiled.unmeasured_compact(),
            compiled.active_physical_qubits,
            compiled.mapping.m2_to_compact,
            snapshot=snapshot,
            limits=ResourceLimits(
                max_qubits=config.max_active_qubits,
                max_memory_mb=config.max_memory_mb,
                max_parallel_threads=config.max_threads,
            ),
        )
        metrics = {}
        if frozen and noise["status"] != "BLOCKED":
            table = build_plan_table(frozen.reference)
            metrics = {
                name: analyze_distribution(
                    frozen.reference, table, np.asarray(values), include_plan_rows=False
                )
                for name, values in (
                    ("device_before_readout", noise["probabilities_before_readout"]),
                    ("device_after_readout", noise["probabilities_after_readout"]),
                )
            }
        prediction = sealed_json(
            destination / "predictions" / f"{circuit_id}.json",
            {
                "artifact_type": "m5_frozen_prediction",
                "circuit_id": circuit_id,
                "source_kind": snapshot["source"],
                "hardware_used": False,
                "target_snapshot_sha256": snapshot["content_sha256"],
                "logical_ideal": ideal.tolist(),
                "compiled_ideal": compiled_ideal.tolist(),
                "device_noise": noise,
                "device_metrics": metrics,
                "frozen_before_results": True,
            },
        )
        record = sealed_json(
            record_path,
            {
                "circuit_id": circuit_id,
                "kind": "main" if frozen else "smoke",
                "reference_name": frozen.reference_name if frozen else None,
                "algorithm": frozen.algorithm if frozen else "basis_smoke",
                "p": frozen.p if frozen else None,
                "num_bits": logical.num_qubits,
                "qpy": metadata,
                "mapping": compiled.mapping.to_dict(),
                "summary": compiled.summary,
                "equivalence": equivalence,
                "prediction_sha256": prediction["content_sha256"],
                "device_noise_status": noise["status"],
            },
        )
        records.append(record)
    prepared = sealed_json(
        destination / "prepared_manifest.json",
        {
            "schema_version": 1,
            "artifact_type": "m5_prepared_circuit_inventory",
            "created_at": draft["created_at"],
            "source_context_sha256": context["content_sha256"],
            "selection_manifest_sha256": selection["content_sha256"],
            "target_snapshot_sha256": snapshot["content_sha256"],
            "backend_name": snapshot["backend_name"],
            "source_kind": snapshot["source"],
            "physical_subset": list(subset),
            "records": records,
            "hardware_enabled": False,
            "submission_approved": False,
            "hardware_used": False,
            **(
                {
                    "selection_manifest_path": selection_path.relative_to(root).as_posix(),
                    "budget_policy_version": config.budget_policy_version,
                    "budget_authorization_sha256": (
                        config.resolved_budget_policy().budget_authorization_sha256
                    ),
                }
                if config.budget_authorization is not None
                else {}
            ),
        },
    )
    for name in ("jobs", "raw", "summaries"):
        (destination / name).mkdir(exist_ok=True)
    checked = verify_local(root, destination)
    return {
        "status": "BLOCKED"
        if any(row["device_noise_status"] == "BLOCKED" for row in records)
        else "OFFLINE_READY"
        if snapshot["source"] != "provider"
        else "LOCAL_READY",
        "resumed": False,
        "hardware_used": False,
        "circuit_count": len(records),
        "prepared_manifest_sha256": prepared["content_sha256"],
        "maximum_probability_absolute_difference": checked[
            "maximum_probability_absolute_difference"
        ],
    }


def verify_local(root: Path, output: str | Path) -> dict[str, Any]:
    root = root.resolve()
    destination = output_directory(root, output)
    prepared = load_prepared(root, destination)
    suite = {item.circuit_id: item for item in reconstruct_suite(root)}
    checks, maximum = [], 0.0
    if (
        len(prepared["records"]) != 28
        or len({row["circuit_id"] for row in prepared["records"]}) != 28
    ):
        raise ValueError("Expected exactly 24 main and four smoke circuits")
    for record in prepared["records"]:
        circuit_id = record["circuit_id"]
        if read_sealed(destination / "circuits" / circuit_id / "record.json") != record:
            raise ValueError("Circuit inventory changed")
        compiled = restore_compilation(root, destination, record)
        logical_meta = record["qpy"]["logical"]
        logical = load_qpy(
            artifact_path(root, logical_meta["path"], destination),
            expected_sha256=logical_meta["sha256"],
        )
        prediction = read_sealed(destination / "predictions" / f"{circuit_id}.json")
        if prediction["content_sha256"] != record["prediction_sha256"]:
            raise ValueError("Frozen prediction changed")
        if compiled.compact_circuit.num_qubits > 10:
            raise ValueError("Active compact width exceeds the fixed guard")
        actual = compiled_ideal_probabilities(compiled)
        if not np.allclose(actual, prediction["compiled_ideal"], rtol=0, atol=1e-10):
            raise ValueError("QPY loaded distribution differs from frozen prediction")
        if not np.allclose(
            logical_probabilities(logical), prediction["logical_ideal"], rtol=0, atol=1e-10
        ):
            raise ValueError("Logical QPY changed distribution")
        if record["kind"] == "main":
            frozen = suite[circuit_id]
            if logical != frozen.circuit:
                raise ValueError("Logical QPY differs from frozen M4 gates/angles")
            check = verify_compilation(frozen, compiled)
            checks.append({"circuit_id": circuit_id, **check})
            maximum = max(maximum, check["maximum_probability_absolute_difference"])
    if len(checks) != 24:
        raise ValueError("Missing frozen main circuit verification")
    result = {
        "artifact_type": "m5_local_verification",
        "status": "PASS",
        "source_kind": prepared["source_kind"],
        "hardware_used": False,
        "prepared_manifest_sha256": prepared["content_sha256"],
        "historical_integrity": historical_integrity(root),
        "checks": checks,
        "maximum_probability_absolute_difference": maximum,
        "qpy_round_trip_verified": True,
        "bit_mapping_verified": True,
        "active_width_maximum": max(row["summary"]["active_width"] for row in prepared["records"]),
        "smoke_circuit_count": 4,
    }
    return sealed_json(destination / "local_verification.json", result)
