"""Read the versioned M2 references without changing or regenerating their QUBOs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from km_quantum.domain import SchedulingInstance
from km_quantum.experiments.io import canonical_hash, read_json
from km_quantum.qubo.decoder import validate_mapping
from km_quantum.qubo.encoder import instance_fingerprint
from km_quantum.qubo.ising import IsingModel, bits_to_spins, qubo_to_ising
from km_quantum.qubo.models import QUBOConfig, QUBOModel, normalize_bits


@dataclass(frozen=True)
class FrozenReference:
    name: str
    instance: SchedulingInstance
    qubo: QUBOModel
    ising: IsingModel
    ground_bits: tuple[str, ...]
    ground_energy: int
    constraint_energy_margin: int | None
    reference_hash: str
    qubo_hash: str
    ising_hash: str
    source_record: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


def validate_reference(reference: FrozenReference) -> None:
    """Check identities and exact coefficients; full minimum completeness is a later gate."""
    model = reference.qubo
    validate_mapping(reference.instance, model.mapping)
    if reference.qubo_hash != canonical_hash(model):
        raise ValueError(f"{reference.name}: frozen QUBO hash mismatch")
    if reference.ising_hash != canonical_hash(reference.ising):
        raise ValueError(f"{reference.name}: frozen Ising hash mismatch")
    if reference.ising != qubo_to_ising(model):
        raise ValueError(f"{reference.name}: frozen Ising coefficients or offset differ from QUBO")
    if not model.penalties.safety_guaranteed:
        raise ValueError(f"{reference.name}: the frozen M3 suite requires safe penalties")
    if type(reference.ground_energy) is not int:
        raise ValueError("ground energy must be an exact integer")
    if reference.constraint_energy_margin is not None and (
        type(reference.constraint_energy_margin) is not int
        or reference.constraint_energy_margin <= 0
    ):
        raise ValueError("frozen feasible references require a strictly positive invalid margin")
    if not reference.ground_bits or reference.ground_bits != tuple(
        sorted(set(reference.ground_bits))
    ):
        raise ValueError("all saved ground strings must be unique, nonempty as a set and sorted")
    for bits in reference.ground_bits:
        state = normalize_bits(bits, model.number_variables)
        if model.energy(state) != reference.ground_energy:
            raise ValueError(f"{reference.name}: saved ground energy differs from QUBO")
        if reference.ising.energy4(bits_to_spins(state)) != 4 * reference.ground_energy:
            raise ValueError(f"{reference.name}: saved ground energy differs from Ising")
    if reference.source_record and (
        canonical_hash(reference.source_record) != reference.reference_hash
    ):
        raise ValueError(f"{reference.name}: frozen source-record hash mismatch")


def _load_inputs(directory: Path) -> dict[str, tuple[SchedulingInstance, QUBOConfig]]:
    manifest = read_json(directory / "suite_manifest.json")
    hand_names = manifest["successful_hand_cases"]
    if (
        not isinstance(hand_names, list)
        or len(hand_names) != 14
        or len(set(hand_names)) != 14
        or manifest["random_case_count"] != 100
        or manifest["random_seed_range"] != [0, 99]
    ):
        raise ValueError("M3 requires the complete frozen M2 suite of 14 hand and 100 random cases")
    hand_files = sorted((directory / "hand_cases").glob("*.json"))
    if {path.stem for path in hand_files} != set(hand_names):
        raise ValueError("frozen hand-case manifest differs from the saved input files")
    inputs: dict[str, tuple[SchedulingInstance, QUBOConfig]] = {}
    for path in hand_files:
        instance = SchedulingInstance.model_validate(read_json(path))
        metadata = instance.metadata.get("reference")
        if not isinstance(metadata, dict) or "qubo_config" not in metadata:
            raise ValueError(f"{path.stem}: hand input is missing its frozen QUBO configuration")
        config = QUBOConfig.model_validate(metadata["qubo_config"])
        inputs[path.stem] = instance, config
    random_inputs = read_json(directory / "random_cases.json")
    expected_names = {f"random-{seed:03d}" for seed in range(100)}
    if not isinstance(random_inputs, dict) or set(random_inputs) != expected_names:
        raise ValueError("all frozen random inputs with seeds 0 through 99 are required")
    for name, raw in random_inputs.items():
        instance = SchedulingInstance.model_validate(raw["instance"])
        if instance.seed != int(name.removeprefix("random-")):
            raise ValueError(f"{name}: input seed disagrees with frozen case name")
        inputs[name] = instance, QUBOConfig.model_validate(raw["config"])
    return inputs


def load_references(root: Path) -> dict[str, FrozenReference]:
    """Load all 114 saved inputs and records from a repository root.

    No original instance is regenerated and no QUBO is re-encoded. Hashes,
    configurations, mappings, saved minima and exact Ising coefficients are
    checked before a reference can enter the state-analysis gate.
    """
    directory = Path(root) / "data" / "synthetic" / "m2"
    inputs = _load_inputs(directory)
    records = [
        json.loads(line)
        for line in (directory / "frozen_qubos.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    names = [record.get("name") for record in records]
    if len(records) != 114 or names != sorted(inputs):
        raise ValueError("frozen records must contain every M2 case exactly once in sorted order")
    required_fields = {
        "schema_version",
        "name",
        "instance_fingerprint",
        "qubo_hash",
        "ising_hash",
        "qubo_model",
        "ising_model",
        "ground_bits",
        "ground_energy",
        "first_excited_energy",
        "best_valid_energy",
        "best_invalid_energy",
        "constraint_energy_margin",
    }
    references = {}
    for record in records:
        name = record["name"]
        if (
            set(record) != required_fields
            or type(record["schema_version"]) is not int
            or (record["schema_version"] != 1)
        ):
            raise ValueError(f"{name}: unsupported or incomplete frozen reference record")
        instance, config = inputs[name]
        model = QUBOModel.model_validate(record["qubo_model"])
        ising = IsingModel.model_validate(record["ising_model"])
        if record["instance_fingerprint"] != instance_fingerprint(instance):
            raise ValueError(f"{name}: frozen record and input fingerprint differ")
        if model.config != config:
            raise ValueError(f"{name}: frozen QUBO configuration differs from saved input config")
        if record["best_valid_energy"] != record["ground_energy"]:
            raise ValueError(f"{name}: frozen best valid energy differs from ground energy")
        invalid = record["best_invalid_energy"]
        margin = None if invalid is None else invalid - record["ground_energy"]
        if margin != record["constraint_energy_margin"]:
            raise ValueError(f"{name}: frozen invalid-energy margin is inconsistent")
        reference = FrozenReference(
            name=name,
            instance=instance,
            qubo=model,
            ising=ising,
            ground_bits=tuple(record["ground_bits"]),
            ground_energy=record["ground_energy"],
            constraint_energy_margin=record["constraint_energy_margin"],
            reference_hash=canonical_hash(record),
            qubo_hash=record["qubo_hash"],
            ising_hash=record["ising_hash"],
            source_record=record,
        )
        validate_reference(reference)
        references[name] = reference
    return references
