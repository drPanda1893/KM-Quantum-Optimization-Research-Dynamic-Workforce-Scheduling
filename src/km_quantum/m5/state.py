"""Small auditable state chain, including interrupted and ambiguous executions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from km_quantum.m4.io import read_json
from km_quantum.m5.io import atomic_json, utc_now

CHAIN = (
    "DRAFT",
    "OFFLINE_READY",
    "PROVIDER_PREFLIGHT_READY",
    "READY_FOR_APPROVAL",
    "APPROVED",
    "SUBMITTED",
    "COLLECTED",
    "ANALYZED",
)
EXCEPTIONS = {"BLOCKED", "PARTIAL", "FAILED", "CANCELLED", "SUBMISSION_UNKNOWN"}


def transition(
    path: Path, target: str, *, reason: str, plan_hash: str | None = None
) -> dict[str, Any]:
    if target not in {*CHAIN, *EXCEPTIONS}:
        raise ValueError("Unknown M5 state")
    data: dict[str, Any] = (
        read_json(path)
        if path.exists()
        else {"artifact_type": "m5_state", "state": "DRAFT", "history": []}
    )
    previous = data["state"]
    if previous == "SUBMISSION_UNKNOWN" and target != previous:
        raise ValueError("Ambiguous submission requires explicit manual reconciliation")
    if target in CHAIN and previous in CHAIN:
        before, after = CHAIN.index(previous), CHAIN.index(target)
        if after != before and after != before + 1:
            # The two repeated jobs re-enter SUBMITTED from COLLECTED.
            if (previous, target) != ("COLLECTED", "SUBMITTED"):
                raise ValueError(f"Invalid M5 state transition {previous} -> {target}")
    if plan_hash is not None and data.get("plan_hash") not in {None, plan_hash}:
        raise ValueError("M5 state is bound to another execution plan")
    if previous != target:
        data["history"].append({"from": previous, "to": target, "at": utc_now(), "reason": reason})
    data.update(state=target, plan_hash=plan_hash or data.get("plan_hash"))
    atomic_json(path, data)
    return data
