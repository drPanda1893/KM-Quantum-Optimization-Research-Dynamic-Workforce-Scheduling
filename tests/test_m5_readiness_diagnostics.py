"""Cached blocked-preflight facts survive acceptance without granting readiness."""

import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from test_m5_readiness import TOOLS, _fixture

from km_quantum.m4.io import read_json
from km_quantum.m5 import readiness
from km_quantum.m5.approvals import plan_hash
from km_quantum.m5.io import atomic_json, raw_hash


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    def deny(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Acceptance diagnostics may never access a provider")

    monkeypatch.setattr(requests.sessions.Session, "request", deny)
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "release", lambda: "synthetic-test-release")


def _blocked(root: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> tuple[Path, dict[str, Any]]:
    output, plan = _fixture(root, monkeypatch, provider=True)
    plan.update(
        artifact_type="m5_hardware_execution_draft",
        provider_verified=False,
        status="BLOCKED",
        diagnostic_kind=kind,
    )
    plan["plan_hash"] = plan_hash(plan)
    atomic_json(output / "execution_plan.json", plan)
    atomic_json(
        output / "provider_preflight.json",
        {
            "source_kind": "ibm_hardware",
            "provider_verified": True,
            "hardware_used": False,
            "status": "BLOCKED",
            "diagnostic_kind": kind,
            "backend": plan["backend"],
            "instance_binding": plan["instance_binding"],
            "plan_hash": plan["plan_hash"],
            "obtained_at": datetime.now(UTC).isoformat(),
            "plan": "open",
            "pricing_type": "free",
            "cost_eur": 0,
            "operational": True,
            "simulator": False,
            "fake": False,
            "usage": {
                "remaining_seconds": 600,
                "consumed_seconds": 0,
                "limit_seconds": 600,
                "usage_limit_reached": False,
            },
            "pending_jobs": {"complete": True, "count": 0, "status": "VERIFIED"},
            "diagnostic": "Authorization: Bearer synthetic-private-value crn:v1:private-fixture",
        },
    )
    return output, plan


@pytest.mark.parametrize(
    "kind",
    [
        "USAGE_LIMIT_EXCEEDED",
        "PROVIDER_CONTRACT_ERROR",
        "LOCAL_CHANNEL_OR_SIMULATION_ERROR",
    ],
)
def test_blocked_draft_preserves_account_and_inventory_diagnostics_without_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    output, _ = _blocked(tmp_path, monkeypatch, kind)
    before = {path: raw_hash(path) for path in tmp_path.rglob("*") if path.is_file()}
    result = readiness.build_acceptance(tmp_path, output, TOOLS)
    assert result["status"] == "BLOCKED"
    assert result["provider_configuration_status"] == "VERIFIED_HISTORICAL"
    assert result["planned_provider_jobs"] == 3 and result["planned_pubs"] == 52
    assert result["planned_main_pubs"] == 48 and result["planned_smoke_pubs"] == 4
    assert result["missing_pubs"] == 52 and result["missing_provider_jobs"] == 3
    assert result["diagnostic_kind"] == kind
    assert result["provider_preflight_observation"]["historical_snapshot"] is True
    assert result["provider_preflight_observation"]["live_read_performed"] is False
    assert result["plan_diagnostics"]["hardware_execution_valid"] is False
    assert result["approval_status"] == "NOT_APPROVED"
    assert result["hardware_execution_status"] == "NOT_STARTED"
    assert result["budget_evidence"]["reserved_qpu_seconds"] == 0
    assert not result["hardware_used"] and not result["raw_results_complete"]
    serialized = json.dumps(result)
    assert "synthetic-private-value" not in serialized and "crn:v1:" not in serialized
    assert before == {path: raw_hash(path) for path in tmp_path.rglob("*") if path.is_file()}


def test_invalid_inventory_draft_cannot_supply_trusted_planned_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, plan = _blocked(tmp_path, monkeypatch, "PROVIDER_CONTRACT_ERROR")
    plan["jobs"][0]["pubs"][0]["shots"] = 127
    plan["plan_hash"] = plan_hash(plan)
    atomic_json(output / "execution_plan.json", plan)
    result = readiness.build_acceptance(tmp_path, output, TOOLS)
    assert result["status"] == "BLOCKED"
    assert result["planned_provider_jobs"] == result["planned_pubs"] == 0
    assert result["approval_status"] == "NOT_APPROVED"


@pytest.mark.parametrize("binding_field", ["instance_binding", "plan_hash"])
def test_cached_wrong_binding_cannot_claim_verified_provider_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, binding_field: str
) -> None:
    output, _ = _blocked(tmp_path, monkeypatch, "USAGE_LIMIT_EXCEEDED")
    cached = read_json(output / "provider_preflight.json")
    cached[binding_field] = "f" * 64
    atomic_json(output / "provider_preflight.json", cached)
    result = readiness.build_acceptance(tmp_path, output, TOOLS)
    assert result["status"] == "BLOCKED"
    assert result["provider_configuration_status"] != "VERIFIED_HISTORICAL"
    assert not result["hardware_used"]
