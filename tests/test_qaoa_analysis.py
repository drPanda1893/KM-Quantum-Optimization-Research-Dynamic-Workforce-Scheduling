"""Independent all-state auditing and honest probabilities for frozen M2 encodings."""

import json
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from km_quantum.domain import SchedulingInstance
from km_quantum.experiments.io import canonical_hash
from km_quantum.qaoa.analysis import analysis_for_probabilities, build_state_table
from km_quantum.qaoa.references import FrozenReference, load_references
from km_quantum.qubo.encoder import encode_qubo
from km_quantum.qubo.ising import qubo_to_ising

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def references() -> dict[str, FrozenReference]:
    return load_references(ROOT)


def test_loads_every_frozen_reference_without_reencoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("saved M2 QUBOs must not be re-encoded")

    monkeypatch.setattr("km_quantum.qubo.encoder.encode_qubo", forbidden)
    references = load_references(ROOT)
    assert len(references) == 114
    assert list(references) == sorted(references)
    assert "O_capacity_and_imbalance" in references
    for reference in references.values():
        assert reference.reference_hash == canonical_hash(reference.source_record)
        assert reference.qubo_hash == canonical_hash(reference.qubo)
        assert reference.ising_hash == canonical_hash(reference.ising)
        assert reference.qubo.mapping.instance_id == reference.instance.instance_id


@pytest.mark.parametrize("tamper", ["hash", "duplicate", "config", "fingerprint"])
def test_loader_rejects_modified_or_incomplete_sources(tmp_path: Path, tamper: str) -> None:
    target = tmp_path / "data" / "synthetic" / "m2"
    shutil.copytree(ROOT / "data" / "synthetic" / "m2", target)
    path = target / "frozen_qubos.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if tamper == "hash":
        rows[0]["qubo_hash"] = "0" * 64
    elif tamper == "duplicate":
        rows[-1] = rows[0]
    elif tamper == "fingerprint":
        rows[0]["instance_fingerprint"] = "0" * 64
    else:
        input_path = target / "hand_cases" / "A_single_job.json"
        raw = json.loads(input_path.read_text(encoding="utf-8"))
        raw["metadata"]["reference"]["qubo_config"]["objective"]["unassigned_penalty"] += 1
        input_path.write_text(json.dumps(raw), encoding="utf-8")
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_references(tmp_path)


@pytest.mark.parametrize(
    "name,ground_count,decoded_count",
    [
        ("H_workload", 2, 4),
        ("K_bounded_slack", 2, 8),
        ("M_active_imbalance", 2, 256),
        ("O_capacity_and_imbalance", 2, 1024),
    ],
)
def test_raw_decoded_optimum_mass_is_separate_from_ground_mass(
    references: dict[str, FrozenReference],
    name: str,
    ground_count: int,
    decoded_count: int,
) -> None:
    reference = references[name]
    table = build_state_table(reference, max_qubits=15)
    assert np.count_nonzero(table.ground) == ground_count
    assert np.count_nonzero(table.decoded_optimal) == decoded_count
    assert np.array_equal(table.ground, table.optimal_valid)
    assert table.classical_objective == reference.ground_energy
    probability = np.full(len(table.bits), 1 / len(table.bits))
    report = analysis_for_probabilities(
        reference,
        table,
        probability,
        float(np.dot(probability, table.energies)),
    )
    assert report["ground_state_probability"] == ground_count / len(table.bits)
    assert report["optimal_valid_probability"] == report["ground_state_probability"]
    assert report["decoded_optimal_schedule_probability"] == decoded_count / len(table.bits)
    assert report["decoded_optimal_schedule_probability"] > report["ground_state_probability"]
    assert report["ground_probability_lift"] == 1
    assert table.checked_ground_set
    assert not table.energies.flags.writeable


def test_zero_penalty_noncanonical_range_can_decode_an_optimal_schedule(
    references: dict[str, FrozenReference],
) -> None:
    reference = references["O_capacity_and_imbalance"]
    table = build_state_table(reference, max_qubits=15)
    index = table.bits.index("011000001010101")
    assert table.encoded_valid[index]
    assert table.decoded_optimal[index]
    assert not table.ground[index] and not table.optimal_valid[index]
    assert table.energies[index] == 1
    assert table.objectives[index] == 0


def test_all_auxiliary_degenerate_minima_are_retained(
    references: dict[str, FrozenReference],
) -> None:
    reference = references["K_bounded_slack"]
    table = build_state_table(reference)
    actual = {bits for bits, ground in zip(table.bits, table.ground, strict=True) if ground}
    assert actual == set(reference.ground_bits) == {"1001001", "1001100"}
    incomplete = replace(reference, ground_bits=(reference.ground_bits[0],), source_record={})
    with pytest.raises(ValueError, match="ground set"):
        build_state_table(incomplete)


def test_constant_offset_is_included_in_state_expectation(
    references: dict[str, FrozenReference],
) -> None:
    reference = references["A_single_job"]
    table = build_state_table(reference)
    assert reference.qubo.constant == 1
    probabilities = np.full(4, 0.25)
    expectation = sum(reference.qubo.energy(bits) / 4 for bits in table.bits)
    report = analysis_for_probabilities(reference, table, probabilities, expectation)
    assert report["expected_energy"] == expectation
    assert report["statevector_expectation_absolute_difference"] == 0
    assert report["expectation_verified"]
    with pytest.raises(ValueError, match="offset-inclusive"):
        analysis_for_probabilities(reference, table, probabilities, expectation - 1)


def test_zero_span_has_zero_normalized_error_and_unit_quality() -> None:
    instance = SchedulingInstance(instance_id="empty", seed=0, workers=(), jobs=())
    model = encode_qubo(instance)
    ising = qubo_to_ising(model)
    reference = FrozenReference(
        name="empty",
        instance=instance,
        qubo=model,
        ising=ising,
        ground_bits=("",),
        ground_energy=0,
        constraint_energy_margin=None,
        reference_hash="empty",
        qubo_hash=canonical_hash(model),
        ising_hash=canonical_hash(ising),
    )
    table = build_state_table(reference)
    report = analysis_for_probabilities(reference, table, [1.0], 0.0)
    assert report["energy_residual"] == report["normalized_energy_error"] == 0
    assert report["normalized_quality"] == report["ground_state_probability"] == 1
    assert report["uniform_ground_probability"] == report["ground_probability_lift"] == 1


@pytest.mark.parametrize(
    "values",
    [[1.0], [[0.25] * 4], [0.2] * 4, [float("nan"), 0, 0, 1], [-0.1, 0.1, 0, 1]],
)
def test_malformed_probabilities_are_rejected(
    references: dict[str, FrozenReference],
    values: list[float],
) -> None:
    reference = references["A_single_job"]
    table = build_state_table(reference)
    with pytest.raises(ValueError, match="probabilities"):
        analysis_for_probabilities(reference, table, values, 0.0)


def test_resource_guard_and_finite_expectation(references: dict[str, FrozenReference]) -> None:
    reference = references["O_capacity_and_imbalance"]
    with pytest.raises(ValueError, match="exceed max_qubits"):
        build_state_table(reference)
    with pytest.raises(ValueError, match="max_qubits"):
        build_state_table(reference, max_qubits=True)
    reference = references["A_single_job"]
    table = build_state_table(reference)
    with pytest.raises(ValueError, match="finite"):
        analysis_for_probabilities(reference, table, [0.25] * 4, float("nan"))
