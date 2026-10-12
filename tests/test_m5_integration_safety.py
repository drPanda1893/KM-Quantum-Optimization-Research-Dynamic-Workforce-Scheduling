"""Public CLI refusal, empty collection and identical-plan resume without IBM."""

from __future__ import annotations

import argparse
import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from test_m5_approval import synthetic_approval, synthetic_plan
from test_m5_planning import prepared_fixture, provider_fixture

from km_quantum.m5 import cli
from km_quantum.m5.approvals import approval_request
from km_quantum.m5.io import atomic_json
from km_quantum.m5.models import M5Config
from km_quantum.m5.planning import make_plan
from km_quantum.m5.provider import utc_now


def _arguments(root: Path, command: str, **overrides: Any) -> argparse.Namespace:
    return argparse.Namespace(
        root=str(root),
        output="results/m5-integration",
        m5_command=command,
        provider_config=None,
        **overrides,
    )


def _deny_provider(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def deny(*args: Any, **kwargs: Any) -> Any:
        calls.append("would-access-provider")
        raise AssertionError("Refusal/empty collect must happen before config or provider access")

    monkeypatch.setattr(cli, "_config", deny)
    monkeypatch.setattr(cli, "IBMRuntimeAdapter", deny)
    monkeypatch.setattr(cli, "preflight", deny)
    return calls


@pytest.mark.parametrize("mutation", ["expired", "mirror", "unsigned", "malformed", "ttl"])
def test_submit_refuses_complete_invalid_human_contract_before_credentials_or_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    output = tmp_path / "results/m5-integration"
    plan = synthetic_plan()
    approval = synthetic_approval(plan)
    now = datetime.now(UTC)
    approval.update(signed_at=now.isoformat(), expires_at=(now + timedelta(hours=1)).isoformat())
    if mutation == "expired":
        approval["expires_at"] = now.isoformat()
    elif mutation == "mirror":
        approval["layout"] = [6, 9, 3]
    elif mutation == "unsigned":
        approval["approved"] = False
    elif mutation == "malformed":
        approval["signed_at"] = "invalid-date"
    else:
        approval["expires_at"] = (now + timedelta(hours=25)).isoformat()
    atomic_json(output / "execution_plan.json", plan)
    approval_path = tmp_path / "local-approval.json"
    atomic_json(approval_path, approval)
    calls = _deny_provider(monkeypatch)
    result, code = cli.execute_m5(_arguments(tmp_path, "submit-next", approval=str(approval_path)))
    assert code == 1 and result["status"] == "BLOCKED"
    assert not calls
    assert not (output / "jobs/ledger.json").exists()


def test_empty_offline_collection_uses_no_credentials_or_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "results/m5-integration"
    prepared = prepared_fixture()
    plan = make_plan(prepared, M5Config())
    atomic_json(output / "execution_plan.json", plan)
    monkeypatch.setattr(cli, "load_prepared", lambda root, output: copy.deepcopy(prepared))
    monkeypatch.setattr(cli, "verify_local", lambda root, output: {"status": "PASS"})
    calls = _deny_provider(monkeypatch)
    result, code = cli.execute_m5(_arguments(tmp_path, "collect"))
    assert code == 0 and result["status"] == "NOT_SUBMITTED"
    assert result["hardware_used"] is False
    assert result["reserved_qpu_seconds"] == 0
    assert len(result["missing_pubs"]) == 52
    assert not calls
    assert not (output / "jobs/ledger.json").exists()


@pytest.mark.parametrize("resume_state", ["READY_FOR_APPROVAL", "PROVIDER_PREFLIGHT_READY"])
def test_same_verified_hardware_plan_command_resumes_without_state_rewind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resume_state: str
) -> None:
    output = tmp_path / "results/m5-integration"
    prepared = prepared_fixture()
    prepared["source_kind"] = "provider"
    for record in prepared["records"]:
        record["device_noise_status"] = "READY"
    evidence = provider_fixture()
    atomic_json(output / "provider_preflight.json", evidence)
    monkeypatch.setattr(cli, "load_prepared", lambda root, output: copy.deepcopy(prepared))
    monkeypatch.setattr(cli, "verify_local", lambda root, output: {"status": "PASS"})
    monkeypatch.setattr(cli, "read_sealed", lambda path: {"calibration_timestamp": utc_now()})
    calls = _deny_provider(monkeypatch)
    first, first_code = cli.execute_m5(_arguments(tmp_path, "plan"))
    assert first_code == 0 and first["status"] == "READY_FOR_APPROVAL"
    plan_bytes = (output / "execution_plan.json").read_bytes()
    request_bytes = (output / "approval_request.json").read_bytes()
    state = json.loads((output / "state.json").read_text(encoding="utf-8"))
    state["state"] = resume_state
    atomic_json(output / "state.json", state)
    second, second_code = cli.execute_m5(_arguments(tmp_path, "plan"))
    assert second_code == 0 and second == first
    assert (output / "execution_plan.json").read_bytes() == plan_bytes
    assert (output / "approval_request.json").read_bytes() == request_bytes
    assert (
        json.loads((output / "state.json").read_text(encoding="utf-8"))["state"]
        == "READY_FOR_APPROVAL"
    )
    assert not calls


@pytest.mark.parametrize("state", ["DRAFT", "PROVIDER_PREFLIGHT_READY", "SUBMISSION_UNKNOWN"])
def test_valid_human_contract_with_invalid_execution_state_refuses_before_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    output = tmp_path / "results/m5-integration"
    prepared = prepared_fixture()
    prepared["source_kind"] = "provider"
    plan = make_plan(prepared, M5Config(), provider_fixture())
    now = datetime.now(UTC)
    approval = approval_request(plan, (now + timedelta(hours=1)).isoformat())
    approval.update(approved=True, signed_by="Florian", signed_at=now.isoformat())
    atomic_json(output / "execution_plan.json", plan)
    atomic_json(
        output / "state.json", {"state": state, "plan_hash": plan["plan_hash"], "history": []}
    )
    approval_path = tmp_path / "local-approval.json"
    atomic_json(approval_path, approval)
    monkeypatch.setattr(cli, "load_prepared", lambda root, output: copy.deepcopy(prepared))
    monkeypatch.setattr(cli, "verify_local", lambda root, output: {"status": "PASS"})
    calls = _deny_provider(monkeypatch)
    result, code = cli.execute_m5(_arguments(tmp_path, "submit-next", approval=str(approval_path)))
    assert code == 1 and result["status"] == "BLOCKED"
    assert not calls
    assert not (output / "jobs/ledger.json").exists()


def test_real_missing_estimates_are_owner_required_in_public_plan_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "results/m5-integration"
    prepared = prepared_fixture()
    prepared["source_kind"] = "provider"
    for record in prepared["records"]:
        record["device_noise_status"] = "READY"
    evidence = provider_fixture()
    evidence["execution_estimates"] = {}
    atomic_json(output / "provider_preflight.json", evidence)
    monkeypatch.setattr(cli, "load_prepared", lambda root, output: copy.deepcopy(prepared))
    monkeypatch.setattr(cli, "verify_local", lambda root, output: {"status": "PASS"})
    monkeypatch.setattr(cli, "read_sealed", lambda path: {"calibration_timestamp": utc_now()})
    calls = _deny_provider(monkeypatch)
    result, code = cli.execute_m5(_arguments(tmp_path, "plan"))
    assert code == 1 and result["status"] == "OWNER_CONFIGURATION_REQUIRED"
    plan = json.loads((output / "execution_plan.json").read_text(encoding="utf-8"))
    assert plan["provider_verified"] is False
    assert plan["hardware_enabled"] is False and plan["submission_approved"] is False
    assert not calls
