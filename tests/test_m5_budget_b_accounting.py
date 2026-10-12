"""Measured quantum usage is charged against reviewed job and campaign bounds."""

from typing import Any

import pytest

from km_quantum.m5.readiness import _usage_budget_diagnostics


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ([20, 80, 80], []),
        ([10, 70, 70], []),
        ([20.01, 70, 70], ["per-job"]),
        ([10, 80.01, 70], ["per-job"]),
        ([20, 80, 80.01], ["per-job", "campaign"]),
        ([None, 70, None], []),
    ],
)
def test_budget_b_actual_accounting(usage: list[float | None], expected: list[str]) -> None:
    jobs = [{"job_id": identity} for identity in ("smoke", "main-block-1", "main-block-2")]
    plan: dict[str, Any] = {
        "jobs": [
            {"id": "smoke", "max_execution_time": 20},
            {"id": "main-block-1", "max_execution_time": 80},
            {"id": "main-block-2", "max_execution_time": 80},
        ],
        "budget": {"max_total_qpu_seconds": 180},
    }
    diagnostics = _usage_budget_diagnostics(jobs, usage, plan)
    assert len(diagnostics) == len(expected)
    assert all(part in diagnostic for part, diagnostic in zip(expected, diagnostics, strict=True))


def test_actual_accounting_cannot_reuse_v1_cap_for_main() -> None:
    jobs = [{"job_id": "main-block-1"}]
    plan = {
        "jobs": [{"id": "main-block-1", "max_execution_time": 40}],
        "budget": {"max_total_qpu_seconds": 120},
    }
    assert "per-job" in _usage_budget_diagnostics(jobs, [70], plan)[0]


def test_unknown_job_cannot_receive_known_jobs_budget() -> None:
    jobs = [{"job_id": "unexpected"}]
    assert "per-job" in _usage_budget_diagnostics(jobs, [0.001], None)[0]
