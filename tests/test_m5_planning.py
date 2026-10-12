"""PUB planning is deterministic, paired, complete and unapproved offline."""

import copy
from typing import Any

import pytest

from km_quantum.m5.approvals import approval_request, plan_hash, validate_approval
from km_quantum.m5.models import M5Config
from km_quantum.m5.planning import make_plan
from km_quantum.m5.provider import SafetyError, utc_now


def prepared_fixture() -> dict[str, Any]:
    """Synthetic prepared inventory; no circuits, accounts or provider are created."""
    subset = [9, 4, 1, 7, 12, 5, 2]
    records = []
    for case, width in (
        ("A_single_job", 2),
        ("E_overlap", 4),
        ("H_workload", 5),
        ("K_bounded_slack", 7),
    ):
        for algorithm, depth in (
            ("standard", 1),
            ("warm_start", 1),
            ("standard", 2),
            ("warm_start", 2),
            ("uniform", None),
            ("warm_product", None),
        ):
            records.append(
                {
                    "circuit_id": f"{case}-{algorithm}-{depth}",
                    "kind": "main",
                    "reference_name": case,
                    "algorithm": algorithm,
                    "p": depth,
                    "num_bits": width,
                }
            )
    records.extend(
        {
            "circuit_id": f"smoke-{index}",
            "kind": "smoke",
            "reference_name": None,
            "algorithm": "basis_smoke",
            "p": None,
            "num_bits": 7,
        }
        for index in range(4)
    )
    for record in records:
        n = record["num_bits"]
        record["mapping"] = {
            "m2_to_logical": list(range(n)),
            "logical_to_initial_physical": subset[:n],
            "m2_to_final_physical": subset[:n],
            "m2_to_classical": list(range(n)),
            "active_physical_qubits": subset[:n],
            "provider_width": 16,
            "register_name": "m2",
        }
        record["qpy"] = {
            "provider": {
                "sha256": "a" * 64,
                "path": "results/m5/circuits/" + record["circuit_id"] + ".qpy",
            }
        }
    return {
        "content_sha256": "1" * 64,
        "selection_manifest_sha256": "2" * 64,
        "source_context_sha256": "3" * 64,
        "target_snapshot_sha256": "4" * 64,
        "backend_name": "offline-synthetic-target",
        "source_kind": "offline_synthetic",
        "physical_subset": subset,
        "records": records,
    }


def provider_fixture() -> dict[str, Any]:
    now = utc_now()
    return {
        "provider_verified": True,
        "source_kind": "ibm_hardware",
        "backend": "offline-synthetic-target",
        "instance_binding": "b" * 64,
        "plan": "open",
        "pricing_type": "free",
        "cost_eur": 0,
        "operational": True,
        "simulator": False,
        "fake": False,
        "runtime_version": "0.47.0",
        "single_job_contract": True,
        "obtained_at": now,
        "usage": {
            "remaining_seconds": 120,
            "consumed_seconds": 0,
            "limit_seconds": 120,
            "observed_at": now,
        },
        "limits": {"max_shots": 1024, "max_jobs": 3, "max_pubs": 24},
        "execution_estimates": {
            identity: {"seconds": 20, "source": "documented synthetic unit-test estimate"}
            for identity in ("smoke", "main-block-1", "main-block-2")
        },
        "rep_delay": {
            "supported": True,
            "value": 0.00025,
            "range": [0, 0.001],
            "source": "Synthetic explicit backend default",
        },
    }


def test_offline_plan_exact_budget_false_flags_and_immutable_sources() -> None:
    prepared = prepared_fixture()
    before = copy.deepcopy(prepared)
    plan = make_plan(prepared, M5Config())
    assert prepared == before
    assert plan["artifact_type"] == "m5_offline_execution_draft"
    assert plan["status"] == "OFFLINE_READY"
    assert plan["backend"] is None and plan["instance_binding"] is None
    assert plan["provider_verified"] is False
    assert plan["hardware_enabled"] is False and plan["submission_approved"] is False
    assert len(plan["jobs"]) == 3 and len(plan["circuits"]) == 28
    assert [len(job["pubs"]) for job in plan["jobs"]] == [4, 24, 24]
    assert sum(pub["shots"] for job in plan["jobs"] for pub in job["pubs"]) == 49664
    assert plan["budget"]["max_total_qpu_seconds"] == 120
    assert all(job["max_execution_time"] == 40 for job in plan["jobs"])
    assert plan_hash(plan) == plan["plan_hash"]
    for key in ("selection_manifest_sha256", "source_context_sha256", "target_snapshot_sha256"):
        assert plan[key] == prepared[key]
    assert plan["prepared_manifest_sha256"] == prepared["content_sha256"]
    approval = approval_request(plan, "2099-01-01T00:00:00+00:00")
    approval.update(approved=True, signed_by="Florian", signed_at=utc_now())
    with pytest.raises(SafetyError, match="concrete verified"):
        validate_approval(plan, approval, provider_fixture())


def test_pair_adjacency_and_full_block_reversal_with_deterministic_seed() -> None:
    prepared = prepared_fixture()
    plan = make_plan(prepared, M5Config())
    first = plan["jobs"][1]["pubs"]
    second = plan["jobs"][2]["pubs"]
    assert [pub["circuit_id"] for pub in second] == [pub["circuit_id"] for pub in reversed(first)]
    identities = {record["circuit_id"]: record for record in prepared["records"]}
    for index in range(0, 24, 2):
        left, right = (
            identities[first[index]["circuit_id"]],
            identities[first[index + 1]["circuit_id"]],
        )
        assert left["reference_name"] == right["reference_name"]
        assert left["p"] == right["p"]
        assert (left["algorithm"], right["algorithm"]) in {
            ("standard", "warm_start"),
            ("uniform", "warm_product"),
        }
    prepared["records"].reverse()
    assert make_plan(prepared, M5Config()) == plan
    assert plan["pub_order_policy"]["seed"] == 20261012
    assert plan["pub_order_policy"]["actual_provider_order_guaranteed"] is False
    assert len({pub["pub_id"] for job in plan["jobs"] for pub in job["pubs"]}) == 52


def test_real_plan_requires_fresh_complete_verified_evidence_and_remains_unsigned() -> None:
    prepared = prepared_fixture()
    prepared["source_kind"] = "provider"
    evidence = provider_fixture()
    plan = make_plan(prepared, M5Config(), evidence)
    assert plan["artifact_type"] == "m5_hardware_execution_plan"
    assert plan["status"] == "READY_FOR_APPROVAL"
    assert plan["provider_verified"] is True
    assert plan["source_kind"] == "ibm_hardware"
    assert plan["hardware_enabled"] is False and plan["submission_approved"] is False
    assert plan["instance_binding"] == evidence["instance_binding"]
    assert plan["runtime_options"]["execution"]["rep_delay"] == 0.00025
    assert all(job["estimated_qpu_seconds"] == 20 for job in plan["jobs"])
    assert "expires_at" not in plan


@pytest.mark.parametrize("change", ["missing", "paid", "fake", "quota", "estimate", "backend"])
def test_real_plan_unknown_or_changed_provider_facts_block(change: str) -> None:
    prepared = prepared_fixture()
    prepared["source_kind"] = "provider"
    evidence: Any = provider_fixture()
    if change == "missing":
        evidence = None
    elif change == "paid":
        evidence["pricing_type"] = "paid"
    elif change == "fake":
        evidence["fake"] = True
    elif change == "quota":
        evidence["usage"]["remaining_seconds"] = 119
    elif change == "estimate":
        evidence["execution_estimates"].pop("smoke")
    else:
        evidence["backend"] = "another-backend"
    if change in {"estimate", "quota"}:
        plan = make_plan(prepared, M5Config(), evidence)
        assert plan["status"] == "OWNER_CONFIGURATION_REQUIRED"
        assert plan["provider_verified"] is False
        request = approval_request(plan, "2099-01-01T00:00:00+00:00")
        request.update(approved=True, signed_by="Florian", signed_at=utc_now())
        with pytest.raises(SafetyError, match="concrete verified"):
            validate_approval(plan, request, evidence)
    else:
        with pytest.raises(SafetyError):
            make_plan(prepared, M5Config(), evidence)


def test_offline_compilation_cannot_be_promoted_by_real_provider_facts() -> None:
    with pytest.raises(SafetyError, match="Synthetic compilation"):
        make_plan(prepared_fixture(), M5Config(), provider_fixture())


@pytest.mark.parametrize(
    "change",
    [
        "missing_hash",
        "unknown_source",
        "duplicate",
        "missing",
        "extra_algorithm",
        "mapping",
        "pair_layout",
    ],
)
def test_changed_prepared_inventory_blocks_plan(change: str) -> None:
    prepared = prepared_fixture()
    if change == "missing_hash":
        prepared.pop("source_context_sha256")
    elif change == "unknown_source":
        prepared["source_kind"] = "unknown"
    elif change == "duplicate":
        prepared["records"][0]["circuit_id"] = prepared["records"][1]["circuit_id"]
    elif change == "missing":
        prepared["records"].pop()
    elif change == "extra_algorithm":
        prepared["records"][0]["algorithm"] = "tuned_after_results"
    elif change == "mapping":
        prepared["records"][0]["mapping"]["m2_to_classical"] = [0, 0]
    else:
        prepared["records"][1]["mapping"]["logical_to_initial_physical"] = [4, 9]
    with pytest.raises(SafetyError):
        make_plan(prepared, M5Config())


@pytest.mark.parametrize("change", ["source", "range", "negative", "infinite"])
def test_rep_delay_requires_documented_supported_range(change: str) -> None:
    prepared = prepared_fixture()
    prepared["source_kind"] = "provider"
    evidence = provider_fixture()
    if change == "source":
        evidence["rep_delay"].pop("source")
    elif change == "range":
        evidence["rep_delay"]["range"] = [0, 0.0001]
    elif change == "negative":
        evidence["rep_delay"]["value"] = -1
    else:
        evidence["rep_delay"]["value"] = float("inf")
    with pytest.raises(SafetyError):
        make_plan(prepared, M5Config(), evidence)


def test_changed_bound_source_or_qpy_hash_changes_plan_hash() -> None:
    prepared = prepared_fixture()
    original = make_plan(prepared, M5Config())
    prepared["records"][0]["qpy"]["provider"]["sha256"] = "c" * 64
    changed = make_plan(prepared, M5Config())
    assert original["plan_hash"] != changed["plan_hash"]
    prepared["source_context_sha256"] = "d" * 64
    assert make_plan(prepared, M5Config())["plan_hash"] != changed["plan_hash"]
