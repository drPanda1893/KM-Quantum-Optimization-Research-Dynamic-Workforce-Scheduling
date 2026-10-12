"""Smoke bit orientation, frozen identities and descriptive zero-hit controls."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from qiskit import ClassicalRegister, QuantumCircuit
from qiskit.primitives.containers import BitArray
from test_m5_planning import prepared_fixture

from km_quantum.m5.approvals import plan_hash
from km_quantum.m5.circuits import smoke_circuits
from km_quantum.m5.io import atomic_json, canonical_hash, read_sealed, sealed_json
from km_quantum.m5.mapping import MeasurementMapping, compact_simulation_copy
from km_quantum.m5.models import M5Config
from km_quantum.m5.planning import make_plan
from km_quantum.m5.provider import IBMRuntimeAdapter, SafetyError
from km_quantum.m5.smoke import analyze_smoke_controls
from km_quantum.m5.snapshots import write_qpy
from km_quantum.qaoa.ordering import m2_bits_to_basis_index


@pytest.fixture(autouse=True)
def prohibit_provider_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def denied(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Smoke description must not connect to accounts or a provider")

    monkeypatch.setattr(IBMRuntimeAdapter, "_connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def _fixture(
    root: Path, *, order: tuple[int, ...] = tuple(range(7))
) -> tuple[Path, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    output = root / "results/m5-smoke-test"
    inventory = prepared_fixture()
    subset = inventory["physical_subset"]
    records = [r for r in inventory["records"] if r["kind"] == "main"]
    for logical in smoke_circuits():
        identity = logical.name
        pattern = logical.metadata["expected_m2_bits"]
        # A nontrivial final physical permutation and compact order preserve
        # the logical M2 pattern while separating all three bit conventions.
        final = tuple(reversed(subset))
        mapping = MeasurementMapping(
            tuple(range(7)), tuple(subset), final, order, tuple(sorted(subset)), 16
        )
        provider = QuantumCircuit(16)
        provider.add_register(ClassicalRegister(7, "m2"))
        for i, bit in enumerate(pattern):
            if bit == "1":
                provider.x(final[i])
        for i, position in enumerate(order):
            provider.measure(final[i], position)
        compact = compact_simulation_copy(provider, mapping)
        folder = output / "circuits" / identity
        qpy = {}
        for kind, circuit in (("logical", logical), ("provider", provider), ("compact", compact)):
            path = folder / f"{kind}.qpy"
            qpy[kind] = {**write_qpy(path, circuit), "path": path.relative_to(root).as_posix()}
        ideal = np.zeros(128)
        index = m2_bits_to_basis_index(pattern)
        ideal[index] = 1
        device = ideal * 0.8
        device[index ^ 1] = 0.2
        prediction = sealed_json(
            output / "predictions" / f"{identity}.json",
            {
                "circuit_id": identity,
                "source_kind": "offline_synthetic",
                "hardware_used": False,
                "frozen_before_results": True,
                "target_snapshot_sha256": inventory["target_snapshot_sha256"],
                "logical_ideal": ideal.tolist(),
                "compiled_ideal": ideal.tolist(),
                "device_noise": {
                    "status": "OFFLINE_READY",
                    "probabilities_after_readout": device.tolist(),
                },
            },
        )
        record = sealed_json(
            folder / "record.json",
            {
                "circuit_id": identity,
                "kind": "smoke",
                "reference_name": None,
                "algorithm": "basis_smoke",
                "p": None,
                "num_bits": 7,
                "qpy": qpy,
                "mapping": mapping.to_dict(),
                "summary": {"active_width": 7},
                "equivalence": {"passed": True, "expected_m2_bits": pattern},
                "prediction_sha256": prediction["content_sha256"],
                "device_noise_status": "OFFLINE_READY",
            },
        )
        records.append(record)
    inventory["records"] = records
    inventory.pop("content_sha256")
    sealed_json(output / "prepared_manifest.json", inventory)
    inventory = read_sealed(output / "prepared_manifest.json")
    plan = make_plan(inventory, M5Config())
    raw = []
    for index, pub in enumerate(plan["jobs"][0]["pubs"]):
        pattern = next(r for r in records if r["circuit_id"] == pub["circuit_id"])["equivalence"][
            "expected_m2_bits"
        ]
        classical = ["0"] * 7
        for variable, position in enumerate(order):
            classical[position] = pattern[variable]
        displayed = "".join(reversed(classical))
        extra = "1111111" if displayed != "1111111" else "0000000"
        raw.append(
            {
                **deepcopy(pub),
                "circuit_sha256": pub["circuit_hash"],
                "pub_index": index,
                "job_id": plan["jobs"][0]["id"],
                "counts": {displayed: 128, extra: 0},
                "source": "mock",
                "hardware_used": False,
            }
        )
    return output, plan, inventory, raw


def _analyze(root: Path, fixture: tuple[Any, ...]) -> dict[str, Any]:
    return analyze_smoke_controls(root, *fixture)


def test_actual_bitarray_named_register_orientation_and_all_counts_retained(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output, plan, prepared, raw = fixture
    control = next(row for row in raw if "1001010" in row["circuit_id"])
    # Real BitArray displays c6..c0, whereas literal M2 is x0..x6.
    control["counts"] = BitArray.from_samples(["0101001"] * 128, num_bits=7).get_counts()
    before = deepcopy((plan, prepared, raw))
    report = _analyze(tmp_path, fixture)
    assert (plan, prepared, raw) == before
    assert report["status"] == "TEST_DATA_ONLY"
    assert report["source_kind"] == "mock"
    assert not report["hardware_used"] and not report["m5_hardware_pass"]
    assert report["content_sha256"] == canonical_hash(
        {key: value for key, value in report.items() if key != "content_sha256"}
    )
    assert report["structural_passed"] and report["ready_for_next_job"]
    assert report["observed_shots"] == report["expected_shots"] == 512
    result = next(r for r in report["controls"] if r["expected_m2_bits"] == "1001010")
    assert result["expected_displayed_bits"] == "0101001"
    assert result["m2_decoded_counts"] == {"1001010": 128}
    assert result["expected_pattern_count"] == 128
    assert result["expected_pattern_frequency"] == 1
    assert result["ideal_expected_pattern_probability"] == 1
    assert result["device_post_readout_expected_pattern_probability"] == 0.8
    assert result["frequency_minus_device_prediction_percentage_points"] == pytest.approx(20)
    for row, observed in zip(report["controls"], raw, strict=True):
        assert row["raw_counts"] == observed["counts"]
        assert sum(row["m2_decoded_counts"].values()) == 128
    assert not (output / "smoke_report.json").exists()


def test_asymmetric_classical_permutation_is_used_instead_of_simple_reversal(
    tmp_path: Path,
) -> None:
    order = (2, 6, 0, 4, 1, 5, 3)
    report = _analyze(tmp_path, _fixture(tmp_path, order=order))
    result = next(r for r in report["controls"] if r["expected_m2_bits"] == "1001010")
    assert result["m2_to_classical"] == list(order)
    assert result["expected_displayed_bits"] == "0110100"
    assert result["expected_displayed_bits"] != result["expected_m2_bits"][::-1]
    assert result["expected_pattern_count"] == 128
    assert result["measured_physical_by_m2"] != result["measured_compact_by_m2"]


def test_bit_reversed_wrong_outcomes_and_zero_hits_are_descriptive_valid_data(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    raw = fixture[3]
    row = next(r for r in raw if "1001010" in r["circuit_id"])
    row["counts"] = {"1001010": 128, "0101001": 0}
    report = _analyze(tmp_path, fixture)
    result = next(r for r in report["controls"] if r["expected_m2_bits"] == "1001010")
    assert result["expected_pattern_count"] == result["expected_pattern_frequency"] == 0
    assert result["m2_decoded_counts"] == {"0101001": 128, "1001010": 0}
    assert result["expected_pattern_confidence_interval_95"][1] > 0
    assert not result["zero_hits_implies_zero_probability"]
    assert report["structural_passed"] and report["ready_for_next_job"]
    assert report["statistical_quality_threshold"] is None
    assert not report["statistical_quality_gate_defined"]
    assert not report["observed_agreement_is_execution_gate"]
    assert not report["postselection_applied"]


@pytest.mark.parametrize(
    "counts",
    [{"0000000": -1}, {"0000000": True}, {"0000000": 127}, {"000000": 128}, {}, {"abcdefg": 128}],
)
def test_invalid_counts_fail_hard_without_sampling_or_repair(tmp_path: Path, counts: Any) -> None:
    fixture = _fixture(tmp_path)
    fixture[3][0]["counts"] = counts
    with pytest.raises(SafetyError):
        _analyze(tmp_path, fixture)


@pytest.mark.parametrize("missing", [1, 4])
def test_missing_pubs_have_explicit_failed_structural_readiness(
    tmp_path: Path, missing: int
) -> None:
    fixture = _fixture(tmp_path)
    fixture[3][:] = fixture[3][:-missing]
    report = _analyze(tmp_path, fixture)
    assert report["status"] == "INCOMPLETE"
    assert not report["structural_passed"] and not report["ready_for_next_job"]
    assert len(report["missing_pub_ids"]) == missing
    assert report["observed_shots"] == (4 - missing) * 128
    assert all(
        row["raw_counts"] is None
        for row in report["controls"]
        if row["pub_id"] in report["missing_pub_ids"]
    )


@pytest.mark.parametrize(
    "change", ["unknown", "duplicate", "swapped_circuit", "hash", "width", "shots", "job", "block"]
)
def test_unknown_swapped_or_tampered_pub_identity_fails_hard(tmp_path: Path, change: str) -> None:
    fixture = _fixture(tmp_path)
    raw = fixture[3]
    if change == "unknown":
        raw[0]["pub_id"] = "unknown-pub"
    elif change == "duplicate":
        raw.append(deepcopy(raw[0]))
    elif change == "swapped_circuit":
        raw[0]["circuit_id"] = raw[1]["circuit_id"]
    elif change == "hash":
        raw[0]["circuit_sha256"] = "0" * 64
    elif change == "width":
        raw[0]["num_bits"] = 6
    elif change == "shots":
        raw[0]["shots"] = 128.0
    elif change == "job":
        raw[0]["job_id"] = "main-block-1"
    else:
        raw[0]["block"] = 1
    with pytest.raises(SafetyError):
        _analyze(tmp_path, fixture)


def test_plan_hash_changes_are_not_silently_rebound(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture[1]["pub_order_policy"]["seed"] += 1
    with pytest.raises(SafetyError, match="plan hash"):
        _analyze(tmp_path, fixture)


@pytest.mark.parametrize(
    "change", ["mock_claims_hardware", "unknown_source", "offline_claims_real"]
)
def test_provenance_is_derived_strictly_without_mock_hardware_claims(
    tmp_path: Path, change: str
) -> None:
    fixture = _fixture(tmp_path)
    row = fixture[3][0]
    if change == "mock_claims_hardware":
        row["hardware_used"] = True
    elif change == "unknown_source":
        row["source"] = "provider"
    else:
        row.update(source="hardware", hardware_used=True)
    with pytest.raises(SafetyError):
        _analyze(tmp_path, fixture)


def test_cumulative_known_main_pubs_do_not_enter_smoke_statistics(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    main = fixture[1]["jobs"][1]
    pub = main["pubs"][0]
    fixture[3].append(
        {
            **pub,
            "job_id": main["id"],
            "counts": {"0" * pub["num_bits"]: pub["shots"]},
            "source": "mock",
            "hardware_used": False,
        }
    )
    report = _analyze(tmp_path, fixture)
    assert report["observed_smoke_pub_count"] == 4
    assert report["observed_shots"] == 512
    assert len(report["controls"]) == 4


def test_actual_mapping_and_frozen_prediction_tamper_are_hard_errors(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output, plan, prepared, _ = fixture
    record = next(r for r in prepared["records"] if r["kind"] == "smoke")
    record["mapping"]["m2_to_classical"] = list(reversed(range(7)))
    record.pop("content_sha256")
    sealed = sealed_json(
        output / "circuits" / record["circuit_id"] / "record.json", record, immutable=False
    )
    record.update(sealed)
    prepared.pop("content_sha256")
    prepared.update(sealed_json(output / "prepared_manifest.json", prepared, immutable=False))
    plan["prepared_manifest_sha256"] = prepared["content_sha256"]
    plan["circuits"][record["circuit_id"]]["mapping"] = deepcopy(record["mapping"])
    plan["plan_hash"] = plan_hash(plan)
    with pytest.raises(SafetyError, match="actual terminal measurements"):
        _analyze(tmp_path, fixture)


def test_prediction_seal_and_qpy_raw_hash_are_checked(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output, _, prepared, _ = fixture
    record = next(r for r in prepared["records"] if r["kind"] == "smoke")
    path = output / "predictions" / f"{record['circuit_id']}.json"
    original = path.read_bytes()
    atomic_json(path, {"circuit_id": record["circuit_id"], "content_sha256": "0" * 64})
    with pytest.raises(SafetyError, match="seal"):
        _analyze(tmp_path, fixture)
    path.write_bytes(original)
    logical_path = tmp_path / record["qpy"]["logical"]["path"]
    logical_path.write_bytes(logical_path.read_bytes() + b"tampered")
    with pytest.raises(SafetyError, match="SHA256"):
        _analyze(tmp_path, fixture)
