"""Frozen ideal-QAOA settings; correctness acceptance is independent of quality."""

from typing import Literal

from pydantic import Field

from km_quantum.domain import FrozenModel


class QAOAConfig(FrozenModel):
    p: int = Field(default=1, ge=1, le=3, strict=True)
    initialization_seed: int = Field(default=0, ge=0, strict=True)
    maxiter: int = Field(default=300, ge=1, strict=True)
    tolerance: float = Field(default=1e-6, gt=0, allow_inf_nan=False)
    rhobeg: float = Field(default=0.5, gt=0, allow_inf_nan=False)
    optimizer: Literal["COBYLA"] = "COBYLA"
    initialization: Literal["uniform[0,pi]"] = "uniform[0,pi]"
    shots: int = Field(default=1024, ge=1, strict=True)
    sampler_seed: int = Field(default=10100, ge=0, strict=True)
    max_qubits: int = Field(default=14, ge=0, le=20, strict=True)


class Evaluation(FrozenModel):
    evaluation: int = Field(ge=1)
    phase: Literal["optimizer", "verification"] = "optimizer"
    parameters: tuple[float, ...]
    expectation: float = Field(allow_inf_nan=False)
    expected_energy: float = Field(allow_inf_nan=False)
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)
    evaluation_runtime_ms: float = Field(ge=0, allow_inf_nan=False)
