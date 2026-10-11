"""Prespecified fresh seeds, unchanged exact oracles and immutable new references."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from km_quantum.experiments.io import canonical_hash
from km_quantum.m4.suite import (
    KNOWN_DIAGNOSTICS,
    NEW_SEEDS,
    create_suite,
    fixed_generator_specs,
    load_m4_references,
    write_suite,
)
from km_quantum.qaoa.references import FrozenReference

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def suite() -> tuple[dict[str, FrozenReference], dict[str, Any]]:
    return create_suite(ROOT)


def test_fixed_new_seeds_and_structural_selection_only() -> None:
    specs = fixed_generator_specs()
    assert len(specs) == 6
    assert tuple(item["generator_config"]["seed"] for item in specs) == NEW_SEEDS
    assert min(NEW_SEEDS) > 99
    assert canonical_hash(specs) == canonical_hash(fixed_generator_specs())
    assert all(item["generator_config"]["must_assign_fraction"] == 0 for item in specs)
    assert specs[-1]["qubo_config"]["objective"]["imbalance_weight"] == 2


def test_suite_proves_all_six_new_instances_with_independent_m1_oracles(
    suite: tuple[dict[str, FrozenReference], dict[str, Any]],
) -> None:
    refs, audit = suite
    assert len(refs) == audit["candidate_count"] == audit["selected_count"] == 12
    assert audit["status"] == "PASS"
    assert audit["structural_rejection_count"] == 0
    assert not audit["selection_uses_qaoa_or_warm_start_results"]
    assert not audit["resampling_or_replacement_performed"]
    assert set(KNOWN_DIAGNOSTICS) <= set(refs)
    for row in audit["selections"]:
        assert row["status"] == "SELECTED"
        assert row["cp_sat_reference"]["status"] == "OPTIMAL"
        assert row["cp_sat_reference"]["objective_value"] == refs[row["reference_id"]].ground_energy
        if row["dataset"] == "new_evaluation":
            v = row["equivalence_verification"]
            assert v["status"] == "PASS"
            assert v["exhaustive_result"]["status"] == "OPTIMAL"
            assert v["ising_result"]["checked_states"] == 2 ** row["qubit_count"]
            assert not row["already_observed_in_m3"]
        else:
            assert row["split"] == "development" and row["already_observed_in_m3"]
    trivial = refs["new_20261105_sparse_skills"]
    assert trivial.qubo.mapping.primary_variable_count == trivial.qubo.number_variables == 2
    assert trivial.qubo.metrics().assignment_variable_count == 0


def test_structural_rejections_are_visible_without_seed_replacement() -> None:
    refs, audit = create_suite(ROOT, max_qubits=7)
    assert len(refs) == 10
    assert audit["candidate_count"] == 12
    rejected = [row for row in audit["selections"] if row["status"] == "REJECTED_STRUCTURE"]
    assert {row["reference_id"] for row in rejected} == {
        "M_active_imbalance",
        "new_20261106_imbalance",
    }
    assert audit["structural_rejection_count"] == 2
    assert all(row["reason"] == "qubit_limit" for row in rejected)
    assert audit["generator_seeds_fixed_before_qaoa"] == list(NEW_SEEDS)


@pytest.mark.parametrize("limit", [0, 11, True, 1.5])
def test_qubit_limit_rejected_before_generation(limit: int) -> None:
    with pytest.raises(ValueError, match="max_qubits"):
        create_suite(ROOT, max_qubits=limit)


def _copy_baseline(tmp_path: Path) -> None:
    shutil.copytree(ROOT / "data" / "synthetic" / "m2", tmp_path / "data" / "synthetic" / "m2")


def test_saved_new_suite_roundtrip_is_exact_and_does_not_reencode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suite: tuple[dict[str, FrozenReference], dict[str, Any]],
) -> None:
    _copy_baseline(tmp_path)
    refs, audit = suite
    write_suite(tmp_path, refs, audit)
    before = (tmp_path / "data/synthetic/m4/references.jsonl").read_bytes()
    write_suite(tmp_path, refs, audit)
    assert (tmp_path / "data/synthetic/m4/references.jsonl").read_bytes() == before

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("loader must use saved coefficients rather than reencoding")

    monkeypatch.setattr("km_quantum.m4.suite.encode_qubo", forbidden)
    loaded = load_m4_references(tmp_path)
    assert set(loaded) == set(refs)
    for name, ref in refs.items():
        assert loaded[name].reference_hash == ref.reference_hash
        assert loaded[name].qubo == ref.qubo
        assert loaded[name].ground_bits == ref.ground_bits


def test_refuses_to_overwrite_frozen_new_suite(
    tmp_path: Path, suite: tuple[dict[str, FrozenReference], dict[str, Any]]
) -> None:
    refs, audit = suite
    write_suite(tmp_path, refs, audit)
    changed = dict(audit, selected_count=1)
    with pytest.raises(ValueError, match="refusing to overwrite"):
        write_suite(tmp_path, refs, changed)


@pytest.mark.parametrize("tamper", ["hash", "selection", "equivalence"])
def test_loader_rejects_new_reference_tampering(
    tmp_path: Path, suite: tuple[dict[str, FrozenReference], dict[str, Any]], tamper: str
) -> None:
    _copy_baseline(tmp_path)
    refs, audit = suite
    write_suite(tmp_path, refs, audit)
    directory = tmp_path / "data/synthetic/m4"
    if tamper == "hash":
        path = directory / "references.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        rows[0]["reference_hash"] = "0" * 64
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    else:
        path = directory / "selection_audit.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        if tamper == "selection":
            saved["selections"][0]["reference_hash"] = "0" * 64
        else:
            saved["status"] = "BLOCKED_EQUIVALENCE"
        path.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(ValueError):
        load_m4_references(tmp_path)
