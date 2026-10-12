"""Before-submit atomic reservations and read-only interrupted-run recovery."""

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister, qpy
from qiskit.primitives.containers import BitArray, DataBin, PrimitiveResult, SamplerPubResult
from test_m5_approval import NOW, synthetic_approval, synthetic_plan, synthetic_preflight

from km_quantum.m5.ledger import JobLedger, parse_sampler_result, validate_counts
from km_quantum.m5.provider import SafetyError


class SyntheticJob:
    def __init__(self, identity: str, pubs: list[Any], *, status: str = "DONE") -> None:
        self.identity = identity
        self.pubs = pubs
        self.current_status = status
        self.result_calls = 0
        self.status_calls = 0

    def job_id(self) -> str:
        return self.identity

    def status(self) -> str:
        self.status_calls += 1
        return self.current_status

    def result(self, *, timeout: float) -> PrimitiveResult[SamplerPubResult]:
        assert timeout == 1
        self.result_calls += 1
        return PrimitiveResult(
            [
                SamplerPubResult(
                    data=DataBin(m2=BitArray.from_samples(["101"] * shots, 3)),
                    metadata={"m5_pub_id": circuit.metadata["m5_pub_id"]},
                )
                for circuit, _, shots in self.pubs
            ],
            metadata={"reported_options": {"provider_note": "actual metadata kept separate"}},
        )

    def metrics(self) -> dict[str, Any]:
        return {"usage": {"quantum_seconds": 2.5}, "timestamps": {"running": NOW}}

    def error_message(self) -> str:
        return "provider failed; Authorization: Bearer never-print-this"


class SyntheticAdapter:
    # This explicit test double exercises the ledger safety boundary. Its output
    # remains mock even though it impersonates the production interface.
    source_kind = "ibm_hardware"
    single_job_contract = True
    hardware_used = False

    def __init__(self, ledger: JobLedger) -> None:
        self.ledger = ledger
        self.calls: list[Any] = []
        self.jobs: dict[str, SyntheticJob] = {}
        self.raise_after_accept = False
        self.current_status = "DONE"

    def metadata(self, config: Any, plan: Any) -> dict[str, Any]:
        raise AssertionError("No provider metadata reads are implicit in ledger unit tests")

    def submit(self, pubs: Any, options: Any, tags: Any) -> SyntheticJob:
        state = self.ledger.snapshot()
        assert state["jobs"][-1]["status"] == "SUBMISSION_UNKNOWN"
        assert state["reserved_qpu_seconds"] == 40 * (len(self.calls) + 1)
        self.calls.append((pubs, options, tags))
        job = SyntheticJob(f"synthetic-job-{len(self.calls)}", pubs, status=self.current_status)
        self.jobs[job.identity] = job
        if self.raise_after_accept:
            raise TimeoutError("Request may have been accepted: token=do-not-leak")
        return job

    def job(self, identity: str) -> SyntheticJob:
        return self.jobs[identity]


@pytest.fixture
def experiment(
    tmp_path: Path,
) -> tuple[dict[str, Any], dict[str, Path], JobLedger, SyntheticAdapter]:
    qreg = QuantumRegister(3, "q")
    creg = ClassicalRegister(3, "m2")
    circuit = QuantumCircuit(qreg, creg)
    circuit.x(0)
    circuit.x(2)
    circuit.measure(qreg, creg)
    path = tmp_path / "frozen.qpy"
    with path.open("wb") as stream:
        qpy.dump(circuit, stream)
    plan = synthetic_plan(hashlib.sha256(path.read_bytes()).hexdigest())
    paths = {identity: path for identity in plan["circuits"]}
    ledger = JobLedger(tmp_path / "jobs" / "ledger.json")
    return plan, paths, ledger, SyntheticAdapter(ledger)


def _submit(experiment: Any) -> dict[str, Any]:
    plan, paths, ledger, adapter = experiment
    return ledger.submit_next(
        plan, synthetic_approval(plan), synthetic_preflight(), adapter, paths, now=NOW
    )


def test_reservation_exists_before_run_and_provider_id_is_persisted(experiment: Any) -> None:
    plan, _, ledger, adapter = experiment
    before = copy.deepcopy(plan)
    record = _submit(experiment)
    assert record["status"] == "SUBMITTED"
    state = ledger.snapshot()
    assert state["jobs"][0]["provider_job_id"] == "synthetic-job-1"
    assert state["reserved_qpu_seconds"] == 40
    assert state["reserved_shots"] == 512
    assert state["submission_approved"] is True
    evidence = state["jobs"][0]["approval_evidence"]
    assert evidence["approved"] is True and evidence["signed_by"] == "Florian"
    assert evidence["validated_at"] == NOW
    assert evidence["plan_hash"] == state["plan_hash"]
    assert len(evidence["approval_sha256"]) == 64
    assert len(evidence["preflight_sha256"]) == 64
    assert state["jobs"][0]["source_kind"] == "mock"
    assert state["jobs"][0]["single_job_contract"] is True
    assert plan == before
    assert len(adapter.calls) == 1
    with pytest.raises(SafetyError, match="Prior job"):
        _submit(experiment)
    assert len(adapter.calls) == 1


def test_accepted_then_timeout_remains_reserved_and_resume_never_resubmits(experiment: Any) -> None:
    plan, paths, ledger, adapter = experiment
    adapter.raise_after_accept = True
    record = _submit(experiment)
    assert record["status"] == "SUBMISSION_UNKNOWN"
    assert record["provider_job_id"] is None
    assert "do-not-leak" not in ledger.path.read_text(encoding="utf-8")
    resumed = JobLedger(ledger.path)
    assert resumed.collect(plan, adapter)["status"] == "SUBMISSION_UNKNOWN"
    with pytest.raises(SafetyError, match="Prior job"):
        resumed.submit_next(
            plan, synthetic_approval(plan), synthetic_preflight(), adapter, paths, now=NOW
        )
    assert len(adapter.calls) == 1
    assert resumed.snapshot()["reserved_qpu_seconds"] == 40


def test_collect_is_one_status_read_and_does_not_wait_for_queued_job(experiment: Any) -> None:
    plan, _, ledger, adapter = experiment
    adapter.current_status = "QUEUED"
    _submit(experiment)
    summary = ledger.collect(plan, adapter)
    job = adapter.jobs["synthetic-job-1"]
    assert job.status_calls == 1
    assert job.result_calls == 0
    assert summary["status"] == "PENDING_HARDWARE"
    assert summary["hardware_used"] is False
    assert len(adapter.calls) == 1


def test_smoke_contract_then_serial_main_jobs_exact_budget_and_mock_status(experiment: Any) -> None:
    plan, _, ledger, adapter = experiment
    for index in range(3):
        assert _submit(experiment)["job_id"] == f"job-{index}"
        summary = ledger.collect(plan, adapter)
        assert summary["job_records"][index]["contract_verified"] is True
    assert summary["status"] == "OFFLINE_READY"
    assert summary["hardware_used"] is False
    assert summary["reserved_qpu_seconds"] == 120
    assert len(summary["raw_results"]) == 52
    assert sum(item["shots"] for item in summary["raw_results"]) == 49664
    assert summary["missing_pubs"] == []
    assert all(item["source"] == "mock" for item in summary["raw_results"])
    assert summary["raw_results"][0]["counts"] == {"101": 128}
    assert summary["raw_results"][0]["provider_metadata"] != plan["runtime_options"]
    with pytest.raises(SafetyError, match="already been reserved"):
        _submit(experiment)
    collected_calls = [job.result_calls for job in adapter.jobs.values()]
    ledger.collect(plan, adapter)
    assert collected_calls == [job.result_calls for job in adapter.jobs.values()]


def test_failed_or_missing_jobs_keep_diagnostics_and_reservation(experiment: Any) -> None:
    plan, _, ledger, adapter = experiment
    _submit(experiment)
    adapter.jobs["synthetic-job-1"].current_status = "ERROR"
    summary = ledger.collect(plan, adapter)
    assert summary["job_records"][0]["status"] == "FAILED"
    assert "never-print-this" not in json.dumps(summary)
    assert summary["reserved_qpu_seconds"] == 40
    with pytest.raises(SafetyError, match="Prior job"):
        _submit(experiment)
    adapter.jobs.clear()
    summary = ledger.collect(plan, adapter)
    assert summary["job_records"][0]["status"] == "PARTIAL"
    assert summary["reserved_qpu_seconds"] == 40
    assert len(adapter.calls) == 1


def test_qpy_bytes_changed_after_approval_blocks_before_reservation(experiment: Any) -> None:
    _, paths, ledger, adapter = experiment
    next(iter(paths.values())).write_bytes(b"changed")
    with pytest.raises(SafetyError, match="QPY bytes"):
        _submit(experiment)
    assert ledger.snapshot()["reserved_qpu_seconds"] == 0
    assert not adapter.calls


def test_unapproved_plan_stops_before_loading_credentials_or_reserving(experiment: Any) -> None:
    plan, paths, ledger, adapter = experiment
    request = synthetic_approval(plan)
    request["approved"] = False
    with pytest.raises(SafetyError, match="explicit approval"):
        ledger.submit_next(plan, request, synthetic_preflight(), adapter, paths, now=NOW)
    assert ledger.snapshot()["reserved_qpu_seconds"] == 0
    assert not adapter.calls


def test_atomic_lock_refuses_competing_submit_without_spending(experiment: Any) -> None:
    _, _, ledger, adapter = experiment
    ledger.lock_path.parent.mkdir(parents=True)
    ledger.lock_path.write_text("other owner", encoding="utf-8")
    with pytest.raises(SafetyError, match="locked"):
        _submit(experiment)
    assert ledger.lock_path.exists()
    assert not adapter.calls


def test_resume_binds_plan_and_persistent_pub_order(experiment: Any) -> None:
    plan, _, ledger, adapter = experiment
    _submit(experiment)
    data = ledger.snapshot()
    data["jobs"][0]["pubs"].reverse()
    ledger.path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SafetyError, match="Persistent PUB"):
        ledger.collect(plan, adapter)
    assert len(adapter.calls) == 1


@pytest.mark.parametrize("corrupt", ["negative", "partial", "bool", "wrong_width", "not_bits"])
def test_raw_counts_contract(corrupt: str) -> None:
    counts: dict[str, Any] = {"001": 128}
    if corrupt == "negative":
        counts = {"001": -1, "111": 129}
    elif corrupt == "partial":
        counts = {"001": 127}
    elif corrupt == "bool":
        counts = {"001": True, "111": 127}
    elif corrupt == "wrong_width":
        counts = {"01": 128}
    else:
        counts = {"0x1": 128}
    with pytest.raises(SafetyError):
        validate_counts(counts, 3, 128)


@pytest.mark.parametrize("corrupt", ["pub_id", "register", "partial", "extra", "shots", "bits"])
def test_sampler_result_persistent_ids_register_and_complete_shots(
    experiment: Any, corrupt: str
) -> None:
    _, _, ledger, adapter = experiment
    record = _submit(experiment)
    good = list(adapter.jobs["synthetic-job-1"].result(timeout=1))
    if corrupt == "pub_id":
        good[0].metadata["m5_pub_id"] = "other-pub"
    elif corrupt == "register":
        good[0] = SamplerPubResult(data=DataBin(meas=good[0].data.m2))
    elif corrupt == "partial":
        good = good[:-1]
    elif corrupt == "extra":
        good.append(good[0])
    elif corrupt == "shots":
        good[0] = SamplerPubResult(data=DataBin(m2=BitArray.from_samples(["101"] * 127, 3)))
    else:
        good[0] = SamplerPubResult(data=DataBin(m2=BitArray.from_samples(["1010"] * 128, 4)))
    parsed, diagnostics = parse_sampler_result(PrimitiveResult(good), record, "mock")
    assert diagnostics
    if corrupt != "extra":
        assert len(parsed) < 4
    assert ledger.snapshot()["reserved_qpu_seconds"] == 40
