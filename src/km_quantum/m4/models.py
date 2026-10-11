"""Bounded M4 configuration frozen before primary quality observations."""

from __future__ import annotations

from pydantic import Field, StrictInt, model_validator

from km_quantum.domain import FrozenModel


class M4Config(FrozenModel):
    depths: tuple[StrictInt, ...] = (1, 2)
    restart_seeds: tuple[StrictInt, ...] = (0, 1, 2, 3, 4)
    max_qubits: int = Field(default=10, ge=0, le=10, strict=True)
    max_instances: int = Field(default=12, ge=1, le=12, strict=True)
    max_training_runs: int = Field(default=240, ge=1, le=240, strict=True)
    max_density_runs: int = Field(default=1200, ge=1, le=1200, strict=True)
    max_memory_mb: int = Field(default=512, ge=128, le=512, strict=True)
    max_parallel_threads: int = Field(default=2, ge=1, le=2, strict=True)
    max_wall_seconds: float = Field(default=7200.0, gt=0, le=7200, allow_inf_nan=False)
    optimizer_maxiter: int = Field(default=300, ge=6, le=300, strict=True)
    optimizer_tolerance: float = Field(default=1e-6, gt=0, allow_inf_nan=False)
    optimizer_rhobeg: float = Field(default=0.5, gt=0, allow_inf_nan=False)
    epsilon: float = Field(default=0.25, gt=0, le=0.5, allow_inf_nan=False)
    relaxation_max_evaluations: int = Field(default=300, ge=1, le=300, strict=True)
    relaxation_max_iterations: int = Field(default=300, ge=1, le=300, strict=True)
    relaxation_ftol: float = Field(default=1e-12, gt=0, allow_inf_nan=False)
    relaxation_gtol: float = Field(default=1e-8, gt=0, allow_inf_nan=False)
    preparation_residual_tolerance: float = Field(default=1e-6, gt=0, allow_inf_nan=False)
    transpiler_seed: int = Field(default=424242, ge=0, strict=True)
    shots: int = Field(default=1024, ge=1, le=4096, strict=True)
    sample_seed_base: int = Field(default=4000000, ge=0, strict=True)
    sampling_alpha: float = Field(default=1e-9, gt=0, lt=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def bounded_depths_and_restarts(self) -> M4Config:
        if self.sampling_alpha != 1e-9:
            raise ValueError("M4 sampling audits freeze alpha=1e-9")
        if not self.depths or any(type(p) is not int or p not in (1, 2) for p in self.depths):
            raise ValueError("Primary M4 depths must be a nonempty subset of p=1,2")
        if len(set(self.depths)) != len(self.depths):
            raise ValueError("Depths must be unique")
        if (
            not self.restart_seeds
            or len(self.restart_seeds) > 5
            or any(type(seed) is not int or seed < 0 for seed in self.restart_seeds)
        ):
            raise ValueError("Use one to five fixed nonnegative restart seeds")
        if len(set(self.restart_seeds)) != len(self.restart_seeds):
            raise ValueError("Restart seeds must be unique")
        return self
