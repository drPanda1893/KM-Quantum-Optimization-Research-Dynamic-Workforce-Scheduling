"""Versioned local budgets, with B requiring an explicit budget-only user record.

Budget authorization permits preparation within its exact envelope. Hardware
approval remains a separate plan-bound decision and is never implied here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import StrictBool, StrictInt, StringConstraints, model_validator

from km_quantum.domain.models import FrozenModel

BUDGET_V1_VERSION: Literal["m5-budget-v1"] = "m5-budget-v1"
BUDGET_B_VERSION: Literal["m5-budget-b-v2"] = "m5-budget-b-v2"
BUDGET_B_SOURCE_HEAD = "5c186536d9b383d66d7a209f2835ccf3748e9b3e"
BUDGET_B_SOURCE_REQUEST_SHA256 = "ec9064416da9a62932ff81dd99cb9ff21077179190edb992d63cdcc7104625da"
JOB_IDS = ("smoke", "main-block-1", "main-block-2")
_SHA256 = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
_TEXT = Annotated[str, StringConstraints(strict=True, min_length=1)]


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def approved_budget_b_payload() -> dict[str, Any]:
    """Describe the exact B envelope; this function itself grants no authorization."""
    return {
        "per_job_qpu_seconds": {"smoke": 20, "main-block-1": 80, "main-block-2": 80},
        "max_total_qpu_seconds": 180,
        "max_provider_jobs": 3,
        "max_pubs": 52,
        "max_total_shots": 49664,
        "max_concurrent_unresolved_own_jobs": 1,
        "max_cost_eur": 0,
        "required_plan": "open",
        "required_pricing_type": "free",
    }


class BudgetAuthorizationProof(FrozenModel):
    """Public local provenance for the human's exact budget-only decision."""

    artifact_type: Literal["m5_budget_authorization"]
    schema_version: StrictInt
    budget_policy_version: Literal["m5-budget-b-v2"]
    scope: Literal["budget_only"]
    approved: StrictBool
    authorized_by: Literal["Florian"]
    authorized_at: _TEXT
    source_head: Literal["5c186536d9b383d66d7a209f2835ccf3748e9b3e"]
    source_request_sha256: Literal[
        "ec9064416da9a62932ff81dd99cb9ff21077179190edb992d63cdcc7104625da"
    ]
    approved_budget: dict[str, Any]
    hardware_enabled: StrictBool
    submission_approved: StrictBool
    hardware_submission_authorized: StrictBool
    content_sha256: _SHA256

    @model_validator(mode="after")
    def exact_budget_only(self) -> Self:
        if self.schema_version != 1 or self.approved is not True:
            raise ValueError("An approved version-one budget authorization record is required")
        if (
            self.hardware_enabled is not False
            or self.submission_approved is not False
            or self.hardware_submission_authorized is not False
        ):
            raise ValueError("Budget authorization cannot enable or approve hardware submission")
        try:
            timestamp = datetime.fromisoformat(self.authorized_at)
        except ValueError:
            raise ValueError("Budget authorization must preserve a valid timestamp") from None
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Budget authorization must preserve a timezone-aware timestamp")
        expected = approved_budget_b_payload()
        if set(self.approved_budget) != set(expected):
            raise ValueError("Budget authorization envelope has missing or unexpected fields")
        caps = self.approved_budget.get("per_job_qpu_seconds")
        if not isinstance(caps, dict) or set(caps) != set(JOB_IDS):
            raise ValueError("Budget authorization requires all three exact job caps")
        if any(type(caps[identity]) is not int for identity in JOB_IDS):
            raise ValueError("Budget authorization job caps must be strict integers")
        for key in (
            "max_total_qpu_seconds",
            "max_provider_jobs",
            "max_pubs",
            "max_total_shots",
            "max_concurrent_unresolved_own_jobs",
        ):
            if type(self.approved_budget[key]) is not int:
                raise ValueError("Budget authorization limits must be strict integers")
        if (
            type(self.approved_budget["max_cost_eur"]) not in {int, float}
            or self.approved_budget != expected
        ):
            raise ValueError("Budget authorization differs from the exact approved B envelope")
        payload = self.model_dump(mode="json", exclude={"content_sha256"})
        if self.content_sha256 != _canonical_hash(payload):
            raise ValueError("Budget authorization content seal mismatch")
        return self


def validate_budget_authorization(
    record: Mapping[str, Any] | BudgetAuthorizationProof,
) -> BudgetAuthorizationProof:
    """Revalidate even an existing object, protecting nested mutable mappings."""
    payload = (
        record.model_dump(mode="json")
        if isinstance(record, BudgetAuthorizationProof)
        else dict(record)
    )
    return BudgetAuthorizationProof.model_validate(payload)


@dataclass(frozen=True)
class BudgetPolicy:
    version: Literal["m5-budget-v1", "m5-budget-b-v2"]
    job_caps: tuple[tuple[str, int], ...]
    max_total_qpu_seconds: int
    budget_authorization_sha256: str | None = None
    max_provider_jobs: int = 3
    max_pubs: int = 52
    max_total_shots: int = 49664
    max_concurrent_unresolved_own_jobs: int = 1
    max_cost_eur: float = 0.0
    required_plan: str = "open"
    required_pricing_type: str = "free"

    def __post_init__(self) -> None:
        expected_caps = (
            tuple((identity, 40) for identity in JOB_IDS)
            if self.version == BUDGET_V1_VERSION
            else (("smoke", 20), ("main-block-1", 80), ("main-block-2", 80))
        )
        if (
            self.version not in {BUDGET_V1_VERSION, BUDGET_B_VERSION}
            or self.job_caps != expected_caps
            or any(type(cap) is not int for _, cap in self.job_caps)
            or self.max_total_qpu_seconds != (120 if self.version == BUDGET_V1_VERSION else 180)
            or type(self.max_total_qpu_seconds) is not int
            or self.max_provider_jobs != 3
            or type(self.max_provider_jobs) is not int
            or self.max_pubs != 52
            or type(self.max_pubs) is not int
            or self.max_total_shots != 49664
            or type(self.max_total_shots) is not int
            or self.max_concurrent_unresolved_own_jobs != 1
            or type(self.max_concurrent_unresolved_own_jobs) is not int
            or self.max_cost_eur != 0
            or type(self.max_cost_eur) not in {int, float}
            or self.required_plan != "open"
            or self.required_pricing_type != "free"
        ):
            raise ValueError("Budget policy must retain its exact versioned envelope")
        if self.version == BUDGET_V1_VERSION and self.budget_authorization_sha256 is not None:
            raise ValueError("V1 cannot carry B authorization")
        if self.version == BUDGET_B_VERSION and (
            not isinstance(self.budget_authorization_sha256, str)
            or len(self.budget_authorization_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.budget_authorization_sha256)
        ):
            raise ValueError("Budget B requires its authorization record binding")

    @property
    def per_job_qpu_seconds(self) -> dict[str, int]:
        return dict(self.job_caps)

    @property
    def max_qpu_seconds_per_job(self) -> int:
        return max(value for _, value in self.job_caps)

    def cap_for_job(self, identity: str) -> int:
        if identity in self.per_job_qpu_seconds:
            return self.per_job_qpu_seconds[identity]
        if self.version == BUDGET_V1_VERSION:
            # Historical local timing helpers also estimate arbitrary synthetic
            # job identities under the same uniform guard. Plans enforce IDs.
            return 40
        raise ValueError("Budget B only authorizes its three fixed persistent job identities")

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "per_job_qpu_seconds": self.per_job_qpu_seconds,
            "max_qpu_seconds_per_job": self.max_qpu_seconds_per_job,
            "max_total_qpu_seconds": self.max_total_qpu_seconds,
            "max_provider_jobs": self.max_provider_jobs,
            "max_pubs": self.max_pubs,
            "max_total_shots": self.max_total_shots,
            "max_concurrent_unresolved_own_jobs": self.max_concurrent_unresolved_own_jobs,
            "max_cost_eur": self.max_cost_eur,
            "required_plan": self.required_plan,
            "required_pricing_type": self.required_pricing_type,
            "budget_authorization_sha256": self.budget_authorization_sha256,
        }


def resolve_budget_policy(
    version: str = BUDGET_V1_VERSION,
    authorization: Mapping[str, Any] | BudgetAuthorizationProof | None = None,
) -> BudgetPolicy:
    """Select an exact version; B has no proof-free or default promotion path."""
    if version == BUDGET_V1_VERSION:
        if authorization is not None:
            raise ValueError("Historical V1 budget must not carry a B authorization record")
        return BudgetPolicy(BUDGET_V1_VERSION, tuple((identity, 40) for identity in JOB_IDS), 120)
    if version != BUDGET_B_VERSION:
        raise ValueError("Unknown M5 budget policy version")
    if authorization is None:
        raise ValueError("Budget B requires the explicit budget-only user authorization record")
    proof = validate_budget_authorization(authorization)
    return BudgetPolicy(
        BUDGET_B_VERSION,
        tuple(
            (identity, proof.approved_budget["per_job_qpu_seconds"][identity])
            for identity in JOB_IDS
        ),
        180,
        budget_authorization_sha256=proof.content_sha256,
    )
