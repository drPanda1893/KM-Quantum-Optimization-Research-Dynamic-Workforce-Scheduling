"""Budget B integration with synthetic contracts and strictly local CLI work.

No synthetic provider facts or locally generated predictions represent hardware
measurements. The real CLI preparation uses only the synthetic offline backend.
"""

from __future__ import annotations

import copy
import socket
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from qiskit import ClassicalRegister, QuantumCircuit
from qiskit.circuit import Measure, Reset
from qiskit.circuit.library import XGate
from qiskit.transpiler import InstructionProperties, Target
from test_m5_planning import prepared_fixture

from km_quantum.__main__ import main
from km_quantum.m4.io import read_json
from km_quantum.m5 import cli
from km_quantum.m5.approvals import (
    approval_request,
    plan_hash,
    validate_human_approval,
    validate_plan,
)
from km_quantum.m5.budget_policy import BUDGET_B_VERSION
from km_quantum.m5.io import atomic_json, canonical_hash, read_sealed
from km_quantum.m5.ledger import JobLedger
from km_quantum.m5.models import M5Config
from km_quantum.m5.planning import make_plan
from km_quantum.m5.provider import (
    IBMRuntimeAdapter,
    SafetyError,
    redact,
    runtime_options_for_job,
)
from km_quantum.m5.provider_limits import assess_plan_limits, plan_payload_hash
from km_quantum.m5.usage import estimate_plan_usage, validate_usage_model
from km_quantum.m5.workflow import load_prepared, load_prepared_config, prepare

ROOT = Path(__file__).resolve().parents[1]
AUTHORIZATION = ROOT / "configs/m5_budget_b_authorization.json"


@pytest.fixture(autouse=True)
def no_account_or_network_access(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Budget B integration must not access accounts, network or hardware")

    monkeypatch.setattr(IBMRuntimeAdapter, "_connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(cli, "_config", forbidden)


@pytest.fixture
def study() -> M5Config:
    config = M5Config.model_validate(read_json(ROOT / "configs/m5_budget_b.json"))
    assert config.budget_authorization is not None
    assert config.budget_authorization.model_dump(mode="json") == read_sealed(AUTHORIZATION)
    return config


def _seal_plan(plan: dict[str, Any]) -> None:
    plan["plan_hash"] = plan_hash(plan)


def _synthetic_hardware_contract(study: M5Config) -> dict[str, Any]:
    """In-memory contract fixture, without a provider or any measured results.

    The normal conservative one-subjob-per-expanded-circuit policy is retained.
    Native timing inputs are synthetic; the real provider is never constructed.
    """
    plan = make_plan(prepared_fixture(), study)
    plan.update(
        artifact_type="m5_hardware_execution_plan",
        provider_verified=True,
        source_kind="ibm_hardware",
        backend="synthetic-contract-only",
        instance_binding="b" * 64,
    )
    target = Target(num_qubits=16)
    properties = {(q,): InstructionProperties(duration=1e-6, error=0) for q in range(16)}
    for operation in (XGate(), Measure(), Reset()):
        target.add_instruction(operation, properties.copy())
    circuits = {}
    for identity, metadata in plan["circuits"].items():
        width = metadata["num_bits"]
        circuit = QuantumCircuit(16)
        circuit.add_register(ClassicalRegister(width, "m2"))
        circuit.x(plan["layout"][0])
        circuit.measure(plan["layout"][:width], circuit.cregs[0])
        circuits[identity] = circuit
    hashes = {identity: item["qpy_sha256"] for identity, item in plan["circuits"].items()}
    delay = {
        "supported": True,
        "value": 0.00025,
        "range": [0, 0.001],
        "source": "Synthetic timing input; no provider observation",
    }
    for options in [plan["runtime_options"], *plan["job_runtime_options"].values()]:
        options["execution"]["rep_delay"] = delay["value"]
    model = estimate_plan_usage(
        plan,
        circuits,
        target,
        rep_delay=delay,
        target_snapshot_sha256=plan["target_snapshot_sha256"],
        qpy_hashes=hashes,
        config=study,
    )
    assert model["status"] == "READY"
    assert all(40 < row["local_policy_seconds"] < 80 for row in model["jobs"][1:])
    assert model["local_policy_total_seconds"] < 180
    plan["local_usage_model"] = model
    plan["usage_model_sha256"] = model["model_sha256"]
    assessment = assess_plan_limits(plan, circuits, qpy_hashes=hashes)
    plan["provider_limit_assessment"] = assessment
    plan["provider_limit_assessment_sha256"] = assessment["content_sha256"]
    for job in plan["jobs"]:
        estimate = model["execution_estimates"][job["id"]]
        job.update(estimated_qpu_seconds=estimate["seconds"], estimate_source=estimate["source"])
    _seal_plan(plan)
    validate_plan(plan)
    validate_usage_model(model, plan)
    return plan


def test_production_b_config_changes_only_budget_and_keeps_v1_scientific_scope(
    study: M5Config,
) -> None:
    historical = M5Config()
    approved = study.model_dump(mode="json")
    for field in (
        "budget_policy_version",
        "budget_authorization",
        "max_qpu_seconds_per_job",
        "max_total_qpu_seconds",
    ):
        approved.pop(field)
    original = historical.model_dump(mode="json")
    original.pop("max_qpu_seconds_per_job")
    original.pop("max_total_qpu_seconds")
    assert approved == original
    inventory = prepared_fixture()
    before = copy.deepcopy(inventory)
    old_plan = make_plan(inventory, historical)
    plan = make_plan(inventory, study)
    assert inventory == before
    assert plan["circuits"] == old_plan["circuits"]
    assert plan["layout"] == old_plan["layout"]
    assert plan["pub_order_policy"] == old_plan["pub_order_policy"]
    assert [job["pubs"] for job in plan["jobs"]] == [job["pubs"] for job in old_plan["jobs"]]
    assert [job["max_execution_time"] for job in plan["jobs"]] == [20, 80, 80]
    assert [len(job["pubs"]) for job in plan["jobs"]] == [4, 24, 24]
    assert sum(pub["shots"] for job in plan["jobs"] for pub in job["pubs"]) == 49664
    assert plan["budget"]["max_total_qpu_seconds"] == 180
    assert plan["plan_hash"] != old_plan["plan_hash"]
    assert not plan["hardware_enabled"] and not plan["submission_approved"]


@pytest.mark.parametrize("change", ["cap", "shots", "hardware_permission", "seal", "version"])
def test_cli_rejects_forged_budget_before_private_configuration_or_provider(
    tmp_path: Path, change: str, capsys: pytest.CaptureFixture[str]
) -> None:
    record = copy.deepcopy(read_sealed(AUTHORIZATION))
    if change == "cap":
        record["approved_budget"]["per_job_qpu_seconds"]["smoke"] = 21
    elif change == "shots":
        record["approved_budget"]["max_total_shots"] = 49665
    elif change == "hardware_permission":
        record["hardware_submission_authorized"] = True
    elif change == "version":
        record["budget_policy_version"] = "arbitrary-budget-version"
    if change != "seal":
        record["content_sha256"] = canonical_hash(
            {key: value for key, value in record.items() if key != "content_sha256"}
        )
    else:
        record["content_sha256"] = "0" * 64
    path = tmp_path / "forged-budget.json"
    atomic_json(path, record)
    output = ROOT / "results" / f"m5-budget-b-refusal-{uuid.uuid4().hex}"
    assert (
        main(
            [
                "m5",
                "provider-preflight",
                "--root",
                str(ROOT),
                "--output",
                str(output),
                "--budget-approval",
                str(path),
            ]
        )
        == 1
    )
    assert "BLOCKED" in capsys.readouterr().out
    assert not output.exists()


def test_new_unsigned_hardware_approval_and_old_approval_cannot_submit(
    study: M5Config, tmp_path: Path
) -> None:
    plan = _synthetic_hardware_contract(study)
    expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    request = approval_request(plan, expiry)
    assert request["approved"] is False
    assert request["budget_authorization"] == read_sealed(AUTHORIZATION)
    assert request["job_runtime_options"] == plan["job_runtime_options"]
    assert not request["hardware_enabled"] and not request["submission_approved"]

    class NoSubmission:
        source_kind = "ibm_hardware"
        single_job_contract = True

        def submit(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("Budget-only authorization cannot reach submission")

    ledger = JobLedger(tmp_path / "ledger.json")
    with pytest.raises(SafetyError, match="explicit approval"):
        ledger.submit_next(plan, request, {}, NoSubmission(), {})  # type: ignore[arg-type]
    assert not ledger.path.exists()
    assert ledger.snapshot()["reserved_qpu_seconds"] == 0
    old_plan = make_plan(prepared_fixture(), M5Config())
    old_request = approval_request(old_plan, expiry)
    old_request.update(approved=True, signed_by="Florian", signed_at=datetime.now(UTC).isoformat())
    with pytest.raises(SafetyError, match="explicit approval"):
        validate_human_approval(plan, old_request)


@pytest.mark.parametrize("change", ["proof", "version", "job_options", "job_cap"])
def test_budget_changes_break_payload_binding_and_validation(study: M5Config, change: str) -> None:
    plan = make_plan(prepared_fixture(), study)
    original_hash = plan_payload_hash(plan)
    modified = copy.deepcopy(plan)
    if change == "proof":
        modified["budget_authorization"]["approved_budget"]["max_total_qpu_seconds"] = 181
    elif change == "version":
        modified["budget_policy_version"] = "m5-budget-v1"
    elif change == "job_options":
        modified["job_runtime_options"]["smoke"]["max_execution_time"] = 80
    else:
        modified["jobs"][0]["max_execution_time"] = 80
    assert plan_payload_hash(modified) != original_hash
    _seal_plan(modified)
    with pytest.raises(SafetyError):
        validate_plan(modified, require_hardware=False)


def test_old_ledger_cannot_transfer_to_b_and_full_public_proof_survives_redaction(
    study: M5Config,
) -> None:
    old_plan = make_plan(prepared_fixture(), M5Config())
    plan = make_plan(prepared_fixture(), study)
    smoke = old_plan["jobs"][0]
    previous = {
        "plan_hash": old_plan["plan_hash"],
        "reserved_qpu_seconds": 40,
        "reserved_shots": 512,
        "jobs": [
            {
                "job_id": "smoke",
                "plan_hash": old_plan["plan_hash"],
                "reserved_qpu_seconds": 40,
                "pubs": copy.deepcopy(smoke["pubs"]),
                "provider_job_id": None,
                "status": "SUBMISSION_UNKNOWN",
            }
        ],
    }
    with pytest.raises(SafetyError, match="different execution plan"):
        JobLedger._check_binding(previous, plan)
    public = redact(
        {
            "budget_authorization": plan["budget_authorization"],
            "budget_authorization_sha256": plan["budget"]["budget_authorization_sha256"],
            "token": "synthetic-secret",
        }
    )
    assert public["budget_authorization"] == read_sealed(AUTHORIZATION)
    assert public["budget_authorization_sha256"] == read_sealed(AUTHORIZATION)["content_sha256"]
    assert public["token"] == "[REDACTED]"
    assert [
        runtime_options_for_job(plan, job["id"])["max_execution_time"] for job in plan["jobs"]
    ] == [
        20,
        80,
        80,
    ]


def test_actual_offline_b_cli_preserves_global_manifest_and_refuses_resume_policy_change(
    study: M5Config, capsys: pytest.CaptureFixture[str]
) -> None:
    historical_path = ROOT / "data/synthetic/m5/selection_manifest.json"
    historical_bytes = historical_path.read_bytes()
    output = ROOT / "results" / f"m5-budget-b-tests-{uuid.uuid4().hex}"
    base = ["--root", str(ROOT), "--output", str(output)]
    assert main(["m5", "prepare", *base, "--budget-approval", str(AUTHORIZATION)]) == 0
    assert load_prepared_config(ROOT, output) == study
    prepared = load_prepared(ROOT, output)
    assert prepared["budget_policy_version"] == BUDGET_B_VERSION
    assert prepared["budget_authorization_sha256"] == read_sealed(AUTHORIZATION)["content_sha256"]
    assert len(prepared["records"]) == 28
    assert len(list((output / "circuits").rglob("*.qpy"))) == 84
    assert historical_path.read_bytes() == historical_bytes
    frozen_files = {path: path.read_bytes() for path in output.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="Prepared budget differs"):
        prepare(ROOT, output, M5Config())
    assert all(path.read_bytes() == contents for path, contents in frozen_files.items())
    assert main(["m5", "plan", *base]) == 0
    plan = read_json(output / "execution_plan.json")
    assert plan["budget_policy_version"] == BUDGET_B_VERSION
    assert plan["budget_authorization"] == read_sealed(AUTHORIZATION)
    assert [row["max_execution_time"] for row in plan["jobs"]] == [20, 80, 80]
    request = read_json(output / "approval_request.json")
    assert request["approved"] is False
    assert request["plan_hash"] == plan["plan_hash"]
    assert main(["m5", "collect", *base]) == 0
    assert read_json(output / "raw/collected.json")["raw_results"] == []
    assert main(["m5", "analyze", *base]) == 0
    analysis = read_sealed(output / "summaries/analysis.json")
    assert analysis["analysis_status"] == "NOT_AVAILABLE"
    assert analysis["hardware_used"] is False
    assert (
        main(["m5", "submit-next", *base, "--approval", str(output / "approval_request.json")]) == 1
    )
    assert not (output / "jobs/ledger.json").exists()
    assert historical_path.read_bytes() == historical_bytes
    assert plan["hardware_enabled"] is False and plan["submission_approved"] is False
    capsys.readouterr()
