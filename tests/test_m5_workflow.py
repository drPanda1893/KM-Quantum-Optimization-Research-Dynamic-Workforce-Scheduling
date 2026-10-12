"""Exercise the actual offline CLI chain and refusal boundaries without IBM calls."""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

import pytest

from km_quantum.__main__ import main
from km_quantum.m4.io import read_json
from km_quantum.m5.cli import COMMANDS, select_physical_subset
from km_quantum.m5.io import atomic_json, historical_integrity, output_directory, read_sealed
from km_quantum.m5.models import M5Config
from km_quantum.m5.provider import IBMRuntimeAdapter
from km_quantum.m5.snapshots import SYNTHETIC_SUBSET, SyntheticBackend
from km_quantum.m5.state import CHAIN, transition
from km_quantum.m5.workflow import load_prepared, prepare, verify_local

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def offline() -> Path:
    output = ROOT / "results" / f"m5-tests-{uuid.uuid4().hex}"
    result = prepare(ROOT, output)
    assert result["circuit_count"] == 28
    return output


@pytest.fixture(autouse=True)
def forbid_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        pytest.fail("Standard local workflow connected to IBM")

    monkeypatch.setattr(IBMRuntimeAdapter, "_connect", denied)


@pytest.mark.parametrize("command", COMMANDS)
def test_cli_help_implemented(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["m5", command, "--help"])
    assert caught.value.code == 0
    assert "--output" in capsys.readouterr().out


def test_offline_artifacts_and_resume(offline: Path) -> None:
    prepared = load_prepared(ROOT, offline)
    assert prepared["source_kind"] == "offline_synthetic"
    assert len([row for row in prepared["records"] if row["kind"] == "main"]) == 24
    assert prepared["hardware_used"] is False
    before = {path: path.read_bytes() for path in offline.rglob("*.qpy")}
    assert len(before) == 84
    assert prepare(ROOT, offline)["resumed"] is True
    assert before == {path: path.read_bytes() for path in before}
    checked = verify_local(ROOT, offline)
    assert len(checked["checks"]) == 24
    assert checked["maximum_probability_absolute_difference"] < 1e-10
    assert checked["active_width_maximum"] <= 7


def test_offline_cli_plan_collect_analyze_refuse(
    offline: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = ["--root", str(ROOT), "--output", str(offline)]
    assert main(["m5", "plan", *base]) == 0
    plan = read_json(offline / "execution_plan.json")
    assert plan["provider_verified"] is False
    assert len(plan["jobs"]) == 3
    assert sum(pub["shots"] for job in plan["jobs"] for pub in job["pubs"]) == 49664
    assert main(["m5", "collect", *base]) == 0
    assert read_json(offline / "raw/collected.json")["raw_results"] == []
    assert main(["m5", "analyze", *base]) == 0
    analysis = read_sealed(offline / "summaries/analysis.json")
    assert analysis["analysis_status"] == "NOT_AVAILABLE"
    assert analysis["hardware_used"] is False
    assert (
        main(["m5", "submit-next", *base, "--approval", str(offline / "approval_request.json")])
        == 1
    )
    assert not (offline / "jobs/ledger.json").exists()
    request = read_json(offline / "approval_request.json")
    assert request["approved"] is False
    assert main(["m5", "plan", *base]) == 0
    capsys.readouterr()


def test_qpy_tampering_not_refrozen(offline: Path) -> None:
    prepared = load_prepared(ROOT, offline)
    path = ROOT / prepared["records"][0]["qpy"]["provider"]["path"]
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"tampered")
        with pytest.raises(ValueError, match="SHA256"):
            prepare(ROOT, offline)
        assert path.read_bytes() == original + b"tampered"
    finally:
        path.write_bytes(original)


def test_prediction_seal_and_inventory_tampering(offline: Path) -> None:
    path = offline / "prepared_manifest.json"
    original = path.read_bytes()
    data = copy.deepcopy(read_json(path))
    data["records"][0]["mapping"]["m2_to_classical"].reverse()
    try:
        atomic_json(path, data)
        with pytest.raises(ValueError, match="seal"):
            load_prepared(ROOT, offline)
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize("requested", ["results/m4", "results", "../m5", "configs/m5"])
def test_historical_output_rejected(requested: str) -> None:
    with pytest.raises(ValueError):
        output_directory(ROOT, requested)


@pytest.mark.parametrize(
    "field,value",
    [
        ("stored_restart_seed", 1),
        ("hardware_enabled", True),
        ("submission_approved", True),
        ("main_shots", 2048),
        ("equivalence_atol", 1e-5),
        ("depths", [1, 2, 3]),
        ("references", ["K_exploratory"]),
    ],
)
def test_frozen_study_cannot_expand(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        M5Config.model_validate({field: value})


def test_historical_protection_is_actual() -> None:
    integrity = historical_integrity(ROOT)
    assert integrity["status"] == "PASS"
    assert integrity["protected_file_count"] == 4176
    assert integrity["protected_raw_bytes_unchanged"] is True


def test_subset_selection_is_structural_and_deterministic() -> None:
    selected = select_physical_subset(SyntheticBackend().target)
    assert selected == SYNTHETIC_SUBSET
    assert select_physical_subset(SyntheticBackend().target) == selected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, True])
def test_subset_rejects_invalid_calibration(value: float) -> None:
    target = SyntheticBackend().target
    target["ecr"][(1, 4)].error = value
    with pytest.raises(ValueError, match="connected"):
        select_physical_subset(target)


def test_state_chain_and_ambiguous_stop(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    for state in CHAIN:
        transition(path, state, reason="test", plan_hash="a" * 64)
    assert read_json(path)["state"] == "ANALYZED"
    other = tmp_path / "ambiguous.json"
    transition(other, "SUBMISSION_UNKNOWN", reason="possible acceptance")
    with pytest.raises(ValueError, match="manual"):
        transition(other, "SUBMITTED", reason="unsafe retry")
    assert read_json(other)["state"] == "SUBMISSION_UNKNOWN"


def test_illegal_transition_and_changed_plan(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    with pytest.raises(ValueError, match="transition"):
        transition(path, "SUBMITTED", reason="missing approval")
    transition(path, "OFFLINE_READY", reason="tested", plan_hash="a" * 64)
    with pytest.raises(ValueError, match="another"):
        transition(path, "PROVIDER_PREFLIGHT_READY", reason="changed", plan_hash="b" * 64)


def test_no_hardware_claim_in_empty_analysis(offline: Path) -> None:
    base = ["--root", str(ROOT), "--output", str(offline)]
    if not (offline / "execution_plan.json").exists():
        assert main(["m5", "plan", *base]) == 0
    assert main(["m5", "analyze", *base]) == 0
    result = json.loads((offline / "summaries/analysis.json").read_text(encoding="utf-8"))
    assert result["m5_pass"] is False
    assert result["observed_hardware_agreement"] == "NOT_AVAILABLE"
