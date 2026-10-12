"""Budget B timing uses named caps and a separately sealed budget-only record."""

from __future__ import annotations

import copy

import pytest
from test_m5_budget_policy import authorization_fixture
from test_m5_usage import circuit, timing_target

from km_quantum.m5.budget_policy import BUDGET_B_VERSION
from km_quantum.m5.io import canonical_hash
from km_quantum.m5.models import M5Config
from km_quantum.m5.usage import estimate_job_usage, estimate_plan_usage, validate_usage_model


def config_b() -> M5Config:
    return M5Config(
        budget_policy_version=BUDGET_B_VERSION,
        budget_authorization=authorization_fixture(),
        max_qpu_seconds_per_job=80,
        max_total_qpu_seconds=180,
    )


def b_plan_and_circuits() -> tuple[dict, dict]:
    config = config_b()
    policy = config.resolved_budget_policy()
    circuits = {
        **{f"smoke-{index}": circuit() for index in range(4)},
        **{f"main-{index}": circuit() for index in range(24)},
    }
    jobs = []
    for identity, kind, count, shots in (
        ("smoke", "smoke", 4, 128),
        ("main-block-1", "main", 24, 1024),
        ("main-block-2", "main", 24, 1024),
    ):
        jobs.append(
            {
                "id": identity,
                "kind": kind,
                "max_execution_time": policy.cap_for_job(identity),
                "pubs": [
                    {
                        "pub_id": f"{identity}-{index}",
                        "circuit_id": f"{kind}-{index}",
                        "circuit_hash": "a" * 64,
                        "shots": shots,
                    }
                    for index in range(count)
                ],
            }
        )
    plan = {
        "jobs": jobs,
        "circuits": {identity: {"qpy_sha256": "a" * 64} for identity in circuits},
        "target_snapshot_sha256": "b" * 64,
        "runtime_options": {"execution": {"init_qubits": True, "rep_delay": 250e-6}},
        "budget_policy_version": BUDGET_B_VERSION,
        "budget_authorization": authorization_fixture(),
    }
    return plan, circuits


def model_for(plan: dict, circuits: dict) -> dict:
    return estimate_plan_usage(
        plan,
        circuits,
        timing_target(),
        rep_delay={
            "supported": True,
            "value": 250e-6,
            "range": [0, 0.002],
            "source": "synthetic explicit backend default",
        },
        target_snapshot_sha256=plan["target_snapshot_sha256"],
        qpy_hashes=dict.fromkeys(circuits, "a" * 64),
        config=config_b(),
    )


def test_b_default_risk_model_now_fits_exact_named_caps_without_reducing_assumptions() -> None:
    plan, circuits = b_plan_and_circuits()
    before = copy.deepcopy(plan)
    model = model_for(plan, circuits)
    assert plan == before
    assert model["status"] == "READY"
    assert model["hard_limits"]["per_job_qpu_seconds"] == {
        "smoke": 20,
        "main-block-1": 80,
        "main-block-2": 80,
    }
    assert model["hard_limits"]["max_total_qpu_seconds"] == 180
    assert model["hard_limits"]["budget_policy_version"] == BUDGET_B_VERSION
    assert (
        model["hard_limits"]["budget_authorization_sha256"]
        == authorization_fixture()["content_sha256"]
    )
    assert [job["local_policy_subjobs"] for job in model["jobs"]] == [4, 24, 24]
    assert all(job["local_policy"]["safety_factor"] == 1.25 for job in model["jobs"])
    assert all(job["rep_delay"]["seconds"] == 250e-6 for job in model["jobs"])
    assert model["jobs"][1]["local_policy_seconds"] > 60
    assert model["jobs"][1]["local_policy_seconds"] < 80
    assert sum(job["total_executions"] for job in model["jobs"]) == 49664
    validate_usage_model(model, plan)


def test_v1_historical_study_shape_and_guard_are_not_promoted_by_b_model() -> None:
    plan, circuits = b_plan_and_circuits()
    b_model = model_for(plan, circuits)
    historical_plan = copy.deepcopy(plan)
    historical_plan.pop("budget_policy_version")
    historical_plan.pop("budget_authorization")
    for job in historical_plan["jobs"]:
        job["max_execution_time"] = 40
    legacy = estimate_plan_usage(
        historical_plan,
        circuits,
        timing_target(),
        rep_delay={
            "supported": True,
            "value": 250e-6,
            "range": [0, 0.002],
            "source": "synthetic explicit backend default",
        },
        target_snapshot_sha256="b" * 64,
        qpy_hashes=dict.fromkeys(circuits, "a" * 64),
    )
    assert legacy["status"] == "BLOCKED"
    assert legacy["hard_limits"] == {
        "max_qpu_seconds_per_job": 40,
        "max_total_qpu_seconds": 120,
        "max_campaign_submissions": 3,
        "max_total_shots": 49664,
    }
    assert "budget_policy_version" not in legacy["jobs"][0]["study"]
    assert legacy["jobs"][1]["config_sha256"] != b_model["jobs"][1]["config_sha256"]
    with pytest.raises(ValueError, match="budget version"):
        validate_usage_model(b_model, historical_plan)


@pytest.mark.parametrize(
    ("identity", "shots", "cap"), [("smoke", 70000, 20), ("main-block-1", 80000, 80)]
)
def test_each_job_uses_its_exact_b_cap_even_when_other_job_cap_is_larger(
    identity: str, shots: int, cap: int
) -> None:
    job = {
        "id": identity,
        "max_execution_time": cap,
        "pubs": [{"pub_id": "test", "circuit_id": "a", "circuit_hash": "a" * 64, "shots": shots}],
    }
    # Smoke exceeds twenty seconds but remains below eighty; main uses forty
    # circuits to produce overhead above eighty without changing the cap.
    if identity == "main-block-1":
        job["pubs"] *= 40
    model = estimate_job_usage(
        job,
        {"a": circuit()},
        timing_target(),
        rep_delay={"supported": True, "value": 250e-6, "range": [0, 0.002], "source": "synthetic"},
        target_snapshot_sha256="b" * 64,
        qpy_hashes={"a": "a" * 64},
        config=config_b(),
    )
    assert model["status"] == "BLOCKED"
    assert model["diagnostic_kind"] == "USAGE_LIMIT_EXCEEDED"
    assert model["local_policy_seconds"] > cap
    if identity == "smoke":
        assert model["local_policy_seconds"] < 80


@pytest.mark.parametrize(
    "change", ["missingproof", "wrongcap", "unknownjob", "wrongproof", "version"]
)
def test_b_models_block_unbound_or_wrong_job_budgets(change: str) -> None:
    plan, circuits = b_plan_and_circuits()
    if change == "missingproof":
        plan.pop("budget_authorization")
    elif change == "wrongcap":
        plan["jobs"][0]["max_execution_time"] = 80
    elif change == "unknownjob":
        plan["jobs"][0]["id"] = "unknown"
    elif change == "wrongproof":
        plan["budget_authorization"]["source_request_sha256"] = "d" * 64
        plan["budget_authorization"]["content_sha256"] = canonical_hash(
            {
                key: value
                for key, value in plan["budget_authorization"].items()
                if key != "content_sha256"
            }
        )
    else:
        plan["budget_policy_version"] = "m5-budget-v1"
    assert model_for(plan, circuits)["status"] == "BLOCKED"


def test_resealed_authorization_is_still_bound_to_model_configuration() -> None:
    plan, circuits = b_plan_and_circuits()
    model = model_for(plan, circuits)
    proof = plan["budget_authorization"]
    proof["authorized_at"] = "2026-10-14T10:00:00+00:00"
    proof["content_sha256"] = canonical_hash(
        {key: value for key, value in proof.items() if key != "content_sha256"}
    )
    with pytest.raises(ValueError, match="authorization"):
        validate_usage_model(model, plan)


def test_budget_permission_never_activates_hardware_flags_in_model_study() -> None:
    plan, circuits = b_plan_and_circuits()
    model = model_for(plan, circuits)
    for job in model["jobs"]:
        assert job["study"]["hardware_enabled"] is False
        assert job["study"]["submission_approved"] is False
        assert job["study"]["budget_authorization"]["hardware_submission_authorized"] is False


def test_b_campaign_over_180_seconds_stays_blocked_with_all_original_shots() -> None:
    plan, circuits = b_plan_and_circuits()
    target = timing_target()
    from qiskit.transpiler import InstructionProperties

    target.update_instruction_properties("x", (0,), InstructionProperties(duration=0.01))
    model = estimate_plan_usage(
        plan,
        circuits,
        target,
        rep_delay={
            "supported": True,
            "value": 250e-6,
            "range": [0, 0.002],
            "source": "synthetic explicit backend default",
        },
        target_snapshot_sha256="b" * 64,
        qpy_hashes=dict.fromkeys(circuits, "a" * 64),
        config=config_b(),
    )
    assert model["status"] == "BLOCKED"
    assert model["local_policy_total_seconds"] > 180
    assert sum(job["total_executions"] for job in model["jobs"]) == 49664
    assert "180-second cap" in " ".join(model["missing"])
    with pytest.raises(ValueError, match="80-second cap"):
        validate_usage_model(model, plan)
