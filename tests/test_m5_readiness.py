"""Read-only fail-closed readiness fixtures; no IBM connection or execution."""

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from km_quantum.m4.io import canonical_hash, lf_file_hash, read_json
from km_quantum.m5 import readiness
from km_quantum.m5.approvals import plan_hash
from km_quantum.m5.io import atomic_json, raw_hash, sealed_json
from km_quantum.m5.provider import safe_runtime_options

TOOLS = {
    name: {"status": "PASS"} for name in ("pytest", "ruff_check", "ruff_format", "mypy", "build")
}


def _fixture(
    root: Path, monkeypatch: pytest.MonkeyPatch, *, provider: bool = False
) -> tuple[Path, dict[str, Any]]:
    output = root / "results/m5"
    output.mkdir(parents=True)
    monkeypatch.setattr(
        readiness,
        "historical_integrity",
        lambda root: {
            "status": "PASS",
            "protected_content_unchanged": True,
        },
    )
    monkeypatch.setattr(readiness, "source_context", lambda root: {"content_sha256": "1" * 64})
    source = "provider" if provider else "offline_synthetic"
    protocol = root / "docs/m5_experiment_protocol.md"
    protocol.parent.mkdir(parents=True)
    protocol.write_text("Test-only precommitted protocol", encoding="utf-8")
    work_order = root / "KM3_M5_IBM_Hardware_Validation_Prompt.md"
    work_order.write_text("Test-only fixed work order", encoding="utf-8")
    selection = sealed_json(
        root / "data/synthetic/m5/selection_manifest.json",
        {
            "protocol_lf_sha256": lf_file_hash(protocol),
            "work_order_sha256": raw_hash(work_order),
        },
    )
    snapshot = sealed_json(output / "target_snapshot.json", {"source": source})
    records = []
    for index in range(28):
        identity = f"test_circuit_{index:02d}"
        kind = "main" if index < 24 else "smoke"
        folder = output / "circuits" / identity
        folder.mkdir(parents=True)
        qpy = {}
        for qpy_kind in ("logical", "provider", "compact"):
            path = folder / f"{qpy_kind}.qpy"
            # Opaque binary bytes: acceptance verifies immutable bytes, while
            # prior local verification is the evidence for QPY round-tripping.
            path.write_bytes(f"test-{identity}-{qpy_kind}".encode())
            qpy[qpy_kind] = {"path": path.relative_to(root).as_posix(), "sha256": raw_hash(path)}
        noise_status = "READY" if provider else "OFFLINE_READY"
        prediction = sealed_json(
            output / "predictions" / f"{identity}.json",
            {
                "circuit_id": identity,
                "source_kind": source,
                "hardware_used": False,
                "frozen_before_results": True,
                "target_snapshot_sha256": snapshot["content_sha256"],
                "logical_ideal": [0.25] * 4,
                "compiled_ideal": [0.25] * 4,
                "device_noise": {
                    "status": noise_status,
                    "coverage": {"verified": True},
                    "readout_applications": 1,
                    "additional_m4_depolarization": False,
                    "compact_to_physical": [7, 19],
                    "measured_compact_by_m2": [1, 0],
                    "probabilities_before_readout": [0.25] * 4,
                    "probabilities_after_readout": [0.25] * 4,
                },
            },
        )
        record = sealed_json(
            folder / "record.json",
            {
                "circuit_id": identity,
                "kind": kind,
                "reference_name": "A_single_job" if kind == "main" else None,
                "num_bits": 2,
                "qpy": qpy,
                "equivalence": {"passed": True},
                "summary": {"active_width": 2},
                "mapping": {"active_physical_qubits": [7, 19], "m2_to_compact": [1, 0]},
                "prediction_sha256": prediction["content_sha256"],
                "device_noise_status": noise_status,
            },
        )
        records.append(record)
    prepared = sealed_json(
        output / "prepared_manifest.json",
        {
            "source_kind": source,
            "hardware_used": False,
            "source_context_sha256": "1" * 64,
            "selection_manifest_sha256": selection["content_sha256"],
            "target_snapshot_sha256": snapshot["content_sha256"],
            "physical_subset": [7, 19],
            "records": records,
        },
    )
    sealed_json(
        output / "local_verification.json",
        {
            "status": "PASS",
            "source_kind": source,
            "prepared_manifest_sha256": prepared["content_sha256"],
            "checks": [
                {"circuit_id": record["circuit_id"], "passed": True} for record in records[:24]
            ],
            "qpy_round_trip_verified": True,
            "bit_mapping_verified": True,
            "active_width_maximum": 2,
            "maximum_probability_absolute_difference": 1e-13,
            "historical_integrity": {"status": "PASS"},
        },
    )
    jobs = []
    for block, kind in ((0, "smoke"), (1, "main"), (2, "main")):
        selected = records[24:] if kind == "smoke" else records[:24]
        jobs.append(
            {
                "id": f"job-{block}",
                "kind": kind,
                "max_execution_time": 40,
                "pubs": [
                    {
                        "pub_id": f"block{block}-{record['circuit_id']}",
                        "circuit_id": record["circuit_id"],
                        "case_name": record["reference_name"],
                        "circuit_hash": record["qpy"]["provider"]["sha256"],
                        "num_bits": 2,
                        "shots": 128 if kind == "smoke" else 1024,
                    }
                    for record in selected
                ],
            }
        )
    plan = {
        "hardware_enabled": False,
        "submission_approved": False,
        "artifact_type": "m5_hardware_execution_plan" if provider else "m5_offline_execution_plan",
        "provider_verified": provider,
        "source_kind": "ibm_hardware" if provider else source,
        "backend": "test_only_backend",
        "instance_binding": "a" * 64,
        "prepared_manifest_sha256": prepared["content_sha256"],
        "selection_manifest_sha256": prepared["selection_manifest_sha256"],
        "source_context_sha256": prepared["source_context_sha256"],
        "target_snapshot_sha256": prepared["target_snapshot_sha256"],
        "layout": [7, 19],
        "budget": {
            "max_provider_jobs": 3,
            "max_total_shots": 49664,
            "max_qpu_seconds_per_job": 40,
            "max_total_qpu_seconds": 120,
            "max_cost_eur": 0,
        },
        "runtime_options": safe_runtime_options(),
        "jobs": jobs,
        "circuits": {
            record["circuit_id"]: {
                "qpy_sha256": record["qpy"]["provider"]["sha256"],
                "path": record["qpy"]["provider"]["path"],
                "mapping": record["mapping"],
                "num_bits": record["num_bits"],
            }
            for record in records
        },
    }
    plan["plan_hash"] = plan_hash(plan)
    atomic_json(output / "execution_plan.json", plan)
    return output, plan


def _execution(
    output: Path, plan: dict[str, Any], *, mock: bool = False, status: str = "COLLECTED"
) -> None:
    source = "mock" if mock else "hardware"
    records = []
    raw = []
    for job in plan["jobs"]:
        record = {
            "job_id": job["id"],
            "plan_hash": plan["plan_hash"],
            "pubs": deepcopy(job["pubs"]),
            "provider_job_id": f"test-only-provider-{job['id']}",
            "status": status,
            "reserved_qpu_seconds": 40,
            "contract_verified": status == "COLLECTED",
            "hardware_used": not mock,
            "source_kind": "mock" if mock else "ibm_hardware",
            "single_job_contract": True,
            "provider_metrics": {"usage": {"quantum_seconds": 0.4}},
            "approval_evidence": {
                "approved": True,
                "signed_by": "Florian",
                "plan_hash": plan["plan_hash"],
                "signed_at": "2026-10-12T10:00:00Z",
                "validated_at": "2026-10-12T10:01:00Z",
                "expires_at": "2026-10-13T10:00:00Z",
                "approval_sha256": "b" * 64,
                "preflight_sha256": "c" * 64,
            },
        }
        if status == "SUBMISSION_UNKNOWN":
            record["provider_job_id"] = None
        if status == "COLLECTED":
            record["raw_results"] = [
                {
                    **deepcopy(pub),
                    "circuit_sha256": pub["circuit_hash"],
                    "job_id": job["id"],
                    "provider_job_id": record["provider_job_id"],
                    "counts": {"00": pub["shots"]},
                    "source": source,
                    "hardware_used": not mock,
                }
                for pub in job["pubs"]
            ]
            raw.extend(record["raw_results"])
        records.append(record)
    atomic_json(
        output / "jobs/ledger.json",
        {
            "artifact_type": "m5_job_ledger",
            "schema_version": 1,
            "plan_hash": plan["plan_hash"],
            "reserved_qpu_seconds": 120,
            "reserved_shots": 49664,
            "jobs": records,
        },
    )
    collected = {"plan_hash": plan["plan_hash"], "job_records": records, "raw_results": raw}
    atomic_json(output / "raw/collected.json", collected)
    if status == "COLLECTED":
        blocks = {
            str(block): [
                {"pub_id": pub["pub_id"], "source": source, "hardware_used": not mock}
                for pub in job["pubs"]
            ]
            for block, job in enumerate(plan["jobs"][1:], start=1)
        }
        sealed_json(
            output / "summaries/analysis.json",
            {
                "analysis_status": "TEST_DATA_ONLY" if mock else "ANALYZED",
                "hardware_used": not mock,
                "plan_hash": plan["plan_hash"],
                "prepared_manifest_sha256": plan["prepared_manifest_sha256"],
                "collected_content_sha256": canonical_hash(collected),
                "blocks": blocks,
                "blocks_pooled": False,
                "analyzed_pub_count": 48,
                "expected_pub_count": 48,
                "real_hardware_pub_count": 0 if mock else 48,
                "missing_pub_ids": [],
                "observed_hardware_agreement": "NOT_AVAILABLE" if mock else "DESCRIPTIVE_ONLY",
            },
        )


def test_offline_ready_requires_actual_tooling_and_builder_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, _ = _fixture(tmp_path, monkeypatch)
    before = {path: raw_hash(path) for path in tmp_path.rglob("*") if path.is_file()}
    pending = readiness.build_acceptance(tmp_path, output)
    assert pending["implementation_status"] == "LOCAL_CHECKS_PENDING"
    assert pending["status"] == "BLOCKED"
    result = readiness.build_acceptance(tmp_path, output, TOOLS)
    assert result["status"] == "OFFLINE_READY"
    assert result["planned_provider_jobs"] == 3 and result["planned_pubs"] == 52
    assert result["planned_main_pubs"] == 48 and result["planned_smoke_pubs"] == 4
    assert result["missing_pubs"] == 52
    assert not result["hardware_used"] and not result["raw_results_complete"]
    assert result["observed_hardware_agreement"] == "NOT_AVAILABLE"
    assert result["provider_configuration_status"] == "OWNER_CONFIGURATION_REQUIRED"
    assert before == {path: raw_hash(path) for path in tmp_path.rglob("*") if path.is_file()}


@pytest.mark.parametrize(
    "target",
    [
        "prepared_manifest.json",
        "local_verification.json",
        "target_snapshot.json",
        "predictions/test_circuit_00.json",
        "circuits/test_circuit_00/provider.qpy",
    ],
)
def test_tampering_any_frozen_layer_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    output, _ = _fixture(tmp_path, monkeypatch)
    with (output / target).open("ab") as stream:
        stream.write(b"tampered")
    result = readiness.build_acceptance(tmp_path, output, TOOLS)
    assert result["status"] == "BLOCKED"
    assert result["gate_diagnostics"]


def test_complete_mock_data_never_pass_hardware(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, plan = _fixture(tmp_path, monkeypatch)
    _execution(output, plan, mock=True)
    result = readiness.build_acceptance(tmp_path, output, TOOLS)
    assert result["status"] == "OFFLINE_READY"
    assert result["hardware_execution_status"] == "TEST_DATA_ONLY"
    assert result["test_data_pubs"] == 52 and result["completed_real_pubs"] == 0
    assert not result["raw_results_complete"] and not result["hardware_used"]
    assert result["analysis_status"] == "TEST_DATA_ONLY"


def test_offline_preparation_cannot_claim_hardware_by_relabeling_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, plan = _fixture(tmp_path, monkeypatch)
    _execution(output, plan, mock=False)
    result = readiness.build_acceptance(tmp_path, output, TOOLS)
    assert result["status"] == "BLOCKED"


@pytest.mark.parametrize(
    "status", ["SUBMITTED", "SUBMISSION_UNKNOWN", "FAILED", "CANCELLED", "PARTIAL"]
)
def test_pending_failed_unknown_states_are_kept_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    output, plan = _fixture(tmp_path, monkeypatch, provider=True)
    _execution(output, plan, status=status)
    result = readiness.build_acceptance(tmp_path, output, TOOLS)
    expected = "PENDING_HARDWARE" if status == "SUBMITTED" else status
    assert result["status"] == expected
    assert result["hardware_execution_status"] == expected
    assert not result["raw_results_complete"]


def test_full_real_fixture_requires_all_evidence_without_quality_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, plan = _fixture(tmp_path, monkeypatch, provider=True)
    _execution(output, plan)
    result = readiness.build_acceptance(tmp_path, output, TOOLS)
    assert result["status"] == "PASS"
    assert result["completed_real_pubs"] == 52 and result["completed_provider_jobs"] == 3
    assert result["raw_results_complete"] and result["hardware_used"]
    assert not result["good_prediction_agreement_required_for_pass"]
    assert result["observed_hardware_agreement"] == "DESCRIPTIVE_ONLY"


@pytest.mark.parametrize("missing", ["approval", "usage", "analysis"])
def test_missing_real_evidence_cannot_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    output, plan = _fixture(tmp_path, monkeypatch, provider=True)
    _execution(output, plan)
    if missing == "analysis":
        (output / "summaries/analysis.json").unlink()
    else:
        ledger = read_json(output / "jobs/ledger.json")
        collected = read_json(output / "raw/collected.json")
        key = "approval_evidence" if missing == "approval" else "provider_metrics"
        ledger["jobs"][0].pop(key)
        collected["job_records"][0].pop(key)
        atomic_json(output / "jobs/ledger.json", ledger)
        atomic_json(output / "raw/collected.json", collected)
        analysis = read_json(output / "summaries/analysis.json")
        analysis["collected_content_sha256"] = canonical_hash(collected)
        sealed_json(
            output / "summaries/analysis.json",
            {key: value for key, value in analysis.items() if key != "content_sha256"},
            immutable=False,
        )
    result = readiness.build_acceptance(tmp_path, output, TOOLS)
    assert result["status"] == "PARTIAL"


def test_raw_negative_counts_or_analysis_binding_tamper_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, plan = _fixture(tmp_path, monkeypatch, provider=True)
    _execution(output, plan)
    analysis = read_json(output / "summaries/analysis.json")
    analysis["collected_content_sha256"] = "f" * 64
    sealed_json(
        output / "summaries/analysis.json",
        {key: value for key, value in analysis.items() if key != "content_sha256"},
        immutable=False,
    )
    assert readiness.build_acceptance(tmp_path, output, TOOLS)["status"] == "BLOCKED"


def test_numeric_analysis_tamper_is_rejected_by_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, plan = _fixture(tmp_path, monkeypatch, provider=True)
    _execution(output, plan)
    analysis = read_json(output / "summaries/analysis.json")
    analysis["analyzed_pub_count"] = 49
    atomic_json(output / "summaries/analysis.json", analysis)
    result = readiness.build_acceptance(tmp_path, output, TOOLS)
    assert result["status"] == "BLOCKED"
    assert any(row.startswith("analysis:") for row in result["gate_diagnostics"])


def test_current_history_failure_blocks_even_with_saved_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, _ = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(readiness, "historical_integrity", lambda root: {"status": "FAIL"})
    result = readiness.build_acceptance(tmp_path, output, TOOLS)
    assert result["status"] == "BLOCKED"
    assert result["historical_integrity"]["status"] == "FAIL"
