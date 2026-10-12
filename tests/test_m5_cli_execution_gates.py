"""Three sequential mock jobs exercise the actual CLI, QPY, smoke and budget gates.

The provider facts are explicitly hypothetical test data. No account, network,
Sampler or hardware is used. Only historical M4/local-source verification is
stubbed: the fixture has basis circuits, not scientific QAOA reference gates.
"""

from __future__ import annotations

import argparse
import copy
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from qiskit import ClassicalRegister, QuantumCircuit
from qiskit.circuit import Measure, Reset
from qiskit.circuit.library import CXGate, XGate
from qiskit.primitives.containers import BitArray, DataBin, PrimitiveResult, SamplerPubResult
from qiskit.transpiler import InstructionProperties, QubitProperties, Target
from test_m5_budget_b_provider import budget_authorization_fixture
from test_m5_planning import provider_fixture
from test_m5_smoke_controls import _fixture

from km_quantum.m5 import cli
from km_quantum.m5.approvals import approval_request
from km_quantum.m5.io import atomic_json, read_json, read_sealed, sealed_json
from km_quantum.m5.ledger import JobLedger
from km_quantum.m5.models import M5Config
from km_quantum.m5.planning import make_plan
from km_quantum.m5.provider import LocalProviderConfig
from km_quantum.m5.provider_limits import assess_plan_limits
from km_quantum.m5.snapshots import load_qpy, snapshot_backend, write_qpy
from km_quantum.m5.usage import estimate_plan_usage


class MockJob:
    def __init__(self, identity: str, pubs: list[Any]) -> None:
        self.identity = identity
        self.pubs = pubs
        self.current_status = "DONE"
        self.actual_usage: Any = 2.5
        self.metrics_calls = 0
        self.result_calls = 0
        self.truncate = False

    def job_id(self) -> str:
        return self.identity

    def status(self) -> str:
        return self.current_status

    def result(self, *, timeout: float) -> Any:
        assert timeout == 1
        self.result_calls += 1
        rows = [
            SamplerPubResult(
                data=DataBin(
                    m2=BitArray.from_samples(
                        ["0" * circuit.num_clbits] * shots,
                        num_bits=circuit.num_clbits,
                    )
                ),
                metadata={"m5_pub_id": circuit.metadata["m5_pub_id"]},
            )
            for circuit, _, shots in self.pubs
        ]
        return PrimitiveResult(rows[:-1] if self.truncate else rows)

    def metrics(self) -> dict[str, Any]:
        self.metrics_calls += 1
        return {"usage": {"quantum_seconds": self.actual_usage}}


@pytest.fixture
def campaign(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    def prohibited(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Offline CLI regression must not access the network or SDK service")

    monkeypatch.setattr(socket, "create_connection", prohibited)
    monkeypatch.setattr(socket.socket, "connect", prohibited)
    import qiskit_ibm_runtime

    monkeypatch.setattr(qiskit_ibm_runtime, "QiskitRuntimeService", prohibited)
    output, _, prepared, _ = _fixture(tmp_path)
    subset = prepared["physical_subset"]
    target = Target(
        num_qubits=16,
        dt=2e-10,
        qubit_properties=[QubitProperties(t1=1e-4, t2=8e-5, frequency=5e9)] * 16,
    )
    for gate, duration in ((XGate(), 1e-6), (Measure(), 2e-6), (Reset(), 3e-6)):
        target.add_instruction(
            gate,
            {(qubit,): InstructionProperties(duration=duration, error=0.01) for qubit in range(16)},
        )
    target.add_instruction(
        CXGate(),
        {
            tuple(edge): InstructionProperties(duration=4e-6, error=0.02)
            for edge in zip(subset[:-1], subset[1:], strict=True)
        },
    )
    now = datetime.now(UTC)
    snapshot = sealed_json(
        output / "target_snapshot.json",
        snapshot_backend(
            SimpleNamespace(
                name=prepared["backend_name"],
                target=target,
                calibration_timestamp=(now - timedelta(minutes=1)).isoformat(),
            ),
            subset,
            source="provider",
        ),
    )
    proof = budget_authorization_fixture()
    study = M5Config(
        budget_policy_version="m5-budget-b-v2",
        budget_authorization=proof,
        max_qpu_seconds_per_job=80,
        max_total_qpu_seconds=180,
    )
    prepared.update(
        source_kind="provider",
        target_snapshot_sha256=snapshot["content_sha256"],
        budget_policy_version=study.budget_policy_version,
        budget_authorization_sha256=proof["content_sha256"],
    )
    for record in prepared["records"]:
        identity = record["circuit_id"]
        if record["kind"] == "main":
            circuit = QuantumCircuit(16)
            circuit.add_register(ClassicalRegister(record["num_bits"], "m2"))
            circuit.x(subset[0])
            for bit, physical in enumerate(subset[: record["num_bits"]]):
                circuit.measure(physical, bit)
            path = output / "circuits" / identity / "provider.qpy"
            record["qpy"]["provider"] = {
                **write_qpy(path, circuit),
                "path": path.relative_to(tmp_path).as_posix(),
            }
        else:
            path = output / "predictions" / f"{identity}.json"
            prediction = read_sealed(path)
            prediction.pop("content_sha256")
            prediction.update(
                source_kind="provider", target_snapshot_sha256=snapshot["content_sha256"]
            )
            prediction["device_noise"]["status"] = "READY"
            # This is fixture construction, before any synthetic result exists.
            path.unlink()
            record["prediction_sha256"] = sealed_json(path, prediction)["content_sha256"]
            record["device_noise_status"] = "READY"
            record.pop("content_sha256")
            record_path = output / "circuits" / identity / "record.json"
            record_path.unlink()
            record.update(sealed_json(record_path, record))
    prepared.pop("content_sha256")
    (output / "prepared_manifest.json").unlink()
    prepared = sealed_json(output / "prepared_manifest.json", prepared)
    config = LocalProviderConfig(
        "crn:v1:synthetic:cli-test-only",
        prepared["backend_name"],
        account_name="synthetic-cli-only",
    )
    evidence = provider_fixture()
    evidence.update(
        instance_binding=config.binding, calibration_timestamp=snapshot["calibration_timestamp"]
    )
    evidence["usage"].update(remaining_seconds=600, limit_seconds=600)
    draft = make_plan(prepared, study, evidence)
    circuits = {
        key: load_qpy(tmp_path / row["path"], expected_sha256=row["qpy_sha256"])
        for key, row in draft["circuits"].items()
    }
    hashes = {key: row["qpy_sha256"] for key, row in draft["circuits"].items()}
    limits = assess_plan_limits(draft, circuits, qpy_hashes=hashes)
    model = estimate_plan_usage(
        draft,
        circuits,
        target,
        rep_delay=evidence["rep_delay"],
        target_snapshot_sha256=snapshot["content_sha256"],
        qpy_hashes=hashes,
        config=study,
    )
    plan = make_plan(prepared, study, evidence, usage_model=model, provider_limit_assessment=limits)
    approval = approval_request(plan, (now + timedelta(hours=1)).isoformat())
    approval.update(approved=True, signed_by="Florian", signed_at=now.isoformat())
    approval_path = tmp_path / "synthetic-approval.json"
    atomic_json(approval_path, approval)
    atomic_json(output / "execution_plan.json", plan)
    atomic_json(
        output / "state.json",
        {"state": "READY_FOR_APPROVAL", "history": [], "plan_hash": plan["plan_hash"]},
    )
    data: dict[str, Any] = {
        "root": tmp_path,
        "output": output,
        "plan": plan,
        "approval": approval_path,
        "jobs": [],
        "device_change": None,
        "config_calls": 0,
        "refresh_calls": 0,
    }

    class MockAdapter:
        source_kind = "ibm_hardware"
        single_job_contract = True
        hardware_used = False

        def __init__(self, actual: Any, **kwargs: Any) -> None:
            assert actual == config
            self.config = actual

        def _secret_values(self) -> tuple[str, ...]:
            return ()

        def fresh_device_snapshot(self, actual_subset: Any) -> dict[str, Any]:
            assert actual_subset == subset
            data["refresh_calls"] += 1
            current = copy.deepcopy(snapshot)
            current.pop("content_sha256")
            current["retrieved_at"] = datetime.now(UTC).isoformat()
            if data["device_change"] == "duration":
                current["instructions"][0]["duration_seconds"] += 1e-9
            from km_quantum.m5.io import canonical_hash

            return {**current, "content_sha256": canonical_hash(current)}

        def metadata(self, actual: Any, actual_plan: Any) -> dict[str, Any]:
            assert actual_plan == plan
            live = copy.deepcopy(evidence)
            time = datetime.now(UTC).isoformat()
            live.update(
                obtained_at=time,
                local_usage_model=model,
                execution_estimates=model["execution_estimates"],
            )
            live["usage"]["observed_at"] = time
            live["pending_jobs"]["observed_at"] = time
            if data["device_change"] == "rep-delay":
                live["rep_delay"]["range"] = [0, 1e-5]
            return live

        def submit(self, pubs: list[Any], options: Any, tags: Any) -> MockJob:
            ledger = JobLedger(output / "jobs/ledger.json").snapshot()
            index = len(data["jobs"])
            assert ledger["jobs"][-1]["status"] == "SUBMISSION_UNKNOWN"
            assert options["max_execution_time"] == (20, 80, 80)[index]
            job = MockJob(f"mock-cli-job-{index + 1}", pubs)
            data["jobs"].append(job)
            return job

        def job(self, identity: str) -> MockJob:
            return next(job for job in data["jobs"] if job.job_id() == identity)

    def local_config(args: Any) -> LocalProviderConfig:
        data["config_calls"] += 1
        return config

    monkeypatch.setattr(cli, "_config", local_config)
    monkeypatch.setattr(cli, "IBMRuntimeAdapter", MockAdapter)
    monkeypatch.setattr(cli, "load_prepared", lambda root, out: copy.deepcopy(prepared))
    monkeypatch.setattr(cli, "verify_local", lambda root, out: {"status": "PASS"})
    return data


def _run(campaign: dict[str, Any], command: str) -> tuple[dict[str, Any], int]:
    return cli.execute_m5(
        argparse.Namespace(
            root=str(campaign["root"]),
            output=str(campaign["output"].relative_to(campaign["root"])),
            m5_command=command,
            approval=str(campaign["approval"]),
            provider_config=None,
        )
    )


def test_three_job_cli_lifecycle_preserves_partial_campaign_and_strict_next_job(
    campaign: Any,
) -> None:
    before = (campaign["output"] / "execution_plan.json").read_bytes()
    for index in range(3):
        record, code = _run(campaign, "submit-next")
        assert code == 0 and record["status"] == "SUBMITTED", record
        collected, code = _run(campaign, "collect")
        assert code == 0 and collected["hardware_used"] is False, collected
        assert collected["campaign_status"] == ("PARTIAL" if index < 2 else "OFFLINE_READY")
        assert collected["next_submission_status"] == (
            "READY_FOR_NEXT_JOB" if index < 2 else "COMPLETE"
        )
        assert read_json(campaign["output"] / "state.json")["state"] == "COLLECTED"
    ledger = JobLedger(campaign["output"] / "jobs/ledger.json").snapshot()
    assert ledger["reserved_qpu_seconds"] == 180 and ledger["reserved_shots"] == 49664
    assert len(collected["raw_results"]) == 52
    assert all(row["hardware_used"] is False for row in collected["raw_results"])
    assert [job.result_calls for job in campaign["jobs"]] == [1, 1, 1]
    assert all(job["approval_evidence"].get("live_device_binding_sha256") for job in ledger["jobs"])
    assert campaign["refresh_calls"] == 3
    assert (campaign["output"] / "execution_plan.json").read_bytes() == before
    result, code = _run(campaign, "submit-next")
    assert code == 1 and result["status"] == "BLOCKED" and len(campaign["jobs"]) == 3


@pytest.mark.parametrize("usage", [None, -1, True, 20.001])
def test_actual_smoke_usage_blocks_before_next_config_or_reservation(
    campaign: Any, usage: Any
) -> None:
    assert _run(campaign, "submit-next")[1] == 0
    campaign["jobs"][0].actual_usage = usage
    collected, _ = _run(campaign, "collect")
    assert collected["next_submission_status"] == "BLOCKED"
    calls = campaign["config_calls"]
    result, code = _run(campaign, "submit-next")
    assert code == 1 and result["status"] == "BLOCKED"
    assert campaign["config_calls"] == calls and len(campaign["jobs"]) == 1
    assert (
        JobLedger(campaign["output"] / "jobs/ledger.json").snapshot()["reserved_qpu_seconds"] == 20
    )


def test_late_usage_refresh_recovers_same_collected_id_without_result_refetch(
    campaign: Any,
) -> None:
    assert _run(campaign, "submit-next")[1] == 0
    job = campaign["jobs"][0]
    job.actual_usage = None
    assert _run(campaign, "collect")[0]["next_submission_status"] == "BLOCKED"
    for _ in range(3):
        assert _run(campaign, "collect")[0]["next_submission_status"] == "BLOCKED"
    job.actual_usage = 2.5
    result, _ = _run(campaign, "collect")
    assert result["next_submission_status"] == "READY_FOR_NEXT_JOB"
    assert job.result_calls == 1 and job.metrics_calls == 5
    assert _run(campaign, "submit-next")[1] == 0 and len(campaign["jobs"]) == 2


@pytest.mark.parametrize("kind", ["pending", "partial-smoke", "duration", "rep-delay"])
def test_unresolved_smoke_or_changed_live_device_never_reserves_main(
    campaign: Any, kind: str
) -> None:
    assert _run(campaign, "submit-next")[1] == 0
    job = campaign["jobs"][0]
    if kind == "pending":
        job.current_status = "RUNNING"
    elif kind == "partial-smoke":
        job.truncate = True
    _run(campaign, "collect")
    if kind in {"duration", "rep-delay"}:
        campaign["device_change"] = kind
    result, code = _run(campaign, "submit-next")
    assert code == 1 and result["status"] == "BLOCKED"
    if kind in {"duration", "rep-delay"}:
        assert result["diagnostic_kind"] == "LIVE_DEVICE_BINDING_ERROR"
        saved = read_json(campaign["output"] / "provider_preflight.json")
        assert saved["status"] == "BLOCKED"
        assessment = saved["live_device_binding"]
        assert assessment["status"] == "BLOCKED"
        archived = campaign["output"] / "preflight/live_device" / assessment["content_sha256"]
        assert read_json(archived / "assessment.json") == assessment
        assert (
            read_json(archived / "target_snapshot.json")["content_sha256"]
            == assessment["current_snapshot_sha256"]
        )
        assert read_json(archived / "provider_preflight.json") == saved
    ledger = JobLedger(campaign["output"] / "jobs/ledger.json").snapshot()
    assert len(campaign["jobs"]) == len(ledger["jobs"]) == 1
    assert ledger["reserved_qpu_seconds"] == 20


def test_mock_smoke_cannot_unlock_real_hardware_main(
    campaign: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _run(campaign, "submit-next")[1] == 0
    assert _run(campaign, "collect")[0]["next_submission_status"] == "READY_FOR_NEXT_JOB"
    factory = cli.IBMRuntimeAdapter

    def real_adapter(*args: Any, **kwargs: Any) -> Any:
        adapter = factory(*args, **kwargs)
        adapter.hardware_used = True
        return adapter

    monkeypatch.setattr(cli, "IBMRuntimeAdapter", real_adapter)
    refreshes = campaign["refresh_calls"]
    result, code = _run(campaign, "submit-next")
    assert code == 1 and result["diagnostic_kind"] == "HARDWARE_PREDECESSOR_REQUIRED"
    assert campaign["refresh_calls"] == refreshes and len(campaign["jobs"]) == 1


def test_removed_main_pub_data_blocks_next_job_before_provider(campaign: Any) -> None:
    for _ in range(2):
        assert _run(campaign, "submit-next")[1] == 0
        assert _run(campaign, "collect")[0]["next_submission_status"] == "READY_FOR_NEXT_JOB"
    path = campaign["output"] / "jobs/ledger.json"
    ledger = read_json(path)
    ledger["jobs"][1]["raw_results"].pop()
    atomic_json(path, ledger)
    calls = campaign["config_calls"]
    result, code = _run(campaign, "submit-next")
    assert code == 1 and result["diagnostic_kind"] == "PRIOR_JOB_DATA_INCOMPLETE"
    assert campaign["config_calls"] == calls and len(campaign["jobs"]) == 2
