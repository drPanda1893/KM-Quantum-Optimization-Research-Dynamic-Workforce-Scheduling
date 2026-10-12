"""Fixed M5 study and finite local resources; no angle-training parameters."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    StrictInt,
    model_serializer,
    model_validator,
)

from km_quantum.domain.models import FrozenModel
from km_quantum.m5.budget_policy import (
    BUDGET_V1_VERSION,
    BudgetAuthorizationProof,
    BudgetPolicy,
    resolve_budget_policy,
)

M4_COMMIT = "4ca11bf8f709aef9f43df4d3f2a0867189b54a7c"
REFERENCES = ("A_single_job", "E_overlap", "H_workload", "K_bounded_slack")


class M5Config(FrozenModel):
    references: tuple[str, ...] = REFERENCES
    depths: tuple[StrictInt, ...] = (1, 2)
    stored_restart_seed: StrictInt = 0
    blocks: StrictInt = 2
    main_shots: StrictInt = 1024
    smoke_shots: StrictInt = 128
    smoke_circuits: StrictInt = 4
    transpiler_seed: StrictInt = 424242
    optimization_level: StrictInt = 1
    pub_order_seed: StrictInt = 20261012
    monte_carlo_seed: StrictInt = 515151
    monte_carlo_samples: StrictInt = 1000
    max_active_qubits: StrictInt = Field(default=10, ge=7, le=10)
    max_memory_mb: StrictInt = Field(default=512, ge=128, le=512)
    max_threads: StrictInt = Field(default=2, ge=1, le=2)
    equivalence_atol: float = 1e-10
    energy_atol: float = 1e-8
    max_provider_jobs: StrictInt = 3
    max_qpu_seconds_per_job: StrictInt = 40
    max_total_qpu_seconds: StrictInt = 120
    max_total_shots: StrictInt = 49664
    max_cost_eur: float = 0.0
    budget_policy_version: Literal["m5-budget-v1", "m5-budget-b-v2"] = BUDGET_V1_VERSION
    budget_authorization: BudgetAuthorizationProof | None = None
    approval_ttl_hours: StrictInt = Field(default=24, ge=1, le=24)
    hardware_enabled: bool = False
    submission_approved: bool = False

    @model_validator(mode="after")
    def fixed_study(self) -> Self:
        policy = self.resolved_budget_policy()
        fixed = {
            "references": REFERENCES,
            "depths": (1, 2),
            "stored_restart_seed": 0,
            "blocks": 2,
            "main_shots": 1024,
            "smoke_shots": 128,
            "smoke_circuits": 4,
            "optimization_level": 1,
            "transpiler_seed": 424242,
            "pub_order_seed": 20261012,
            "monte_carlo_seed": 515151,
            "monte_carlo_samples": 1000,
            "max_provider_jobs": 3,
            "max_qpu_seconds_per_job": policy.max_qpu_seconds_per_job,
            "max_total_qpu_seconds": policy.max_total_qpu_seconds,
            "max_total_shots": 49664,
            "max_cost_eur": 0.0,
            "equivalence_atol": 1e-10,
            "energy_atol": 1e-8,
            "hardware_enabled": False,
            "submission_approved": False,
        }
        for key, expected in fixed.items():
            if getattr(self, key) != expected:
                raise ValueError(f"The initial M5 study fixes {key}={expected!r}")
        return self

    def resolved_budget_policy(self) -> BudgetPolicy:
        return resolve_budget_policy(self.budget_policy_version, self.budget_authorization)

    def execution_time_cap(self, identity: str) -> int:
        return self.resolved_budget_policy().cap_for_job(identity)

    @model_serializer(mode="wrap")
    def preserve_historical_serialization(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        payload = dict(handler(self))
        if self.budget_policy_version == BUDGET_V1_VERSION:
            # Original study/config hashes stay verifiable without refreezing
            # old artifacts. B always serializes its explicit version and proof.
            payload.pop("budget_policy_version", None)
            payload.pop("budget_authorization", None)
        return payload
