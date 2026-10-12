"""Read-only CLI refreshes live usage after slow local work and preserves snapshot time."""

import argparse
import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from qiskit import ClassicalRegister, QuantumCircuit
from qiskit.circuit import Measure, Reset
from qiskit.circuit.library import XGate
from qiskit.transpiler import InstructionProperties, Target
from test_m5_planning import prepared_fixture, provider_fixture

from km_quantum.m5 import cli, provider
from km_quantum.m5.io import read_json
from km_quantum.m5.provider import LocalProviderConfig


@pytest.mark.parametrize("fail_noise", [False, True])
def test_slow_local_work_refreshes_account_without_retimestamping_calibration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_noise: bool
) -> None:
    clock = [datetime(2026, 10, 12, 12, tzinfo=UTC)]
    monkeypatch.setattr(provider, "utc_now", lambda: clock[0].isoformat())
    prepared = prepared_fixture()
    prepared["source_kind"] = "provider"
    for row in prepared["records"]:
        row["qpy"]["provider"]["path"] = (
            "results/provider-fresh/circuits/" + row["circuit_id"] + ".qpy"
        )
    backend_target = Target(num_qubits=16)
    properties = {(index,): InstructionProperties(duration=1e-6, error=0) for index in range(16)}
    for operation in (XGate(), Reset(), Measure()):
        backend_target.add_instruction(operation, properties.copy())
    backend = type("ReadOnlyTestBackend", (), {"target": backend_target})()
    calls: list[str] = []
    timestamp = "2026-10-11T08:00:00+00:00"
    snapshot = {
        "instructions": [],
        "qubits": [],
        "physical_subset": prepared["physical_subset"],
        "backend_name": prepared["backend_name"],
        "backend_width": 16,
        "calibration_timestamp": timestamp,
        "retrieved_at": "2026-10-12T11:00:00+00:00",
        "operation_names": ["measure", "reset", "x"],
        "target_timing": {"dt_seconds": None},
        "readout_matrix_convention": "rows=true, columns=observed",
    }

    class ReadOnlyAdapter:
        source_kind = "ibm_hardware"
        single_job_contract = True

        def __init__(self, config: Any) -> None:
            self.config = config

        def _secret_values(self) -> tuple[str, ...]:
            return ()

        def metadata(self, config: Any, plan: Any) -> dict[str, Any]:
            calls.append("account" if plan is None else "plan")
            evidence = provider_fixture()
            now = clock[0].isoformat()
            evidence.update(obtained_at=now, calibration_timestamp=timestamp)
            evidence["usage"]["observed_at"] = now
            evidence["pending_jobs"]["observed_at"] = now
            if plan is not None:
                evidence["local_usage_model"] = copy.deepcopy(plan["local_usage_model"])
                evidence["execution_estimates"] = copy.deepcopy(
                    plan["local_usage_model"]["execution_estimates"]
                )
            return evidence

        def refresh_backend_for_snapshot(self) -> Any:
            return backend

        def fresh_device_snapshot(self, subset: Any) -> dict[str, Any]:
            assert list(subset) == prepared["physical_subset"]
            return copy.deepcopy(snapshot)

        def submit(self, *args: Any) -> None:
            raise AssertionError("No submission is authorized or needed")

    config = LocalProviderConfig(
        "crn:v1:synthetic:fixture", prepared["backend_name"], account_name="synthetic-only"
    )
    monkeypatch.setattr(cli, "_config", lambda args: config)
    monkeypatch.setattr(cli, "IBMRuntimeAdapter", ReadOnlyAdapter)
    monkeypatch.setattr(
        cli, "select_physical_subset", lambda target: tuple(prepared["physical_subset"])
    )
    monkeypatch.setattr(cli, "load_prepared", lambda root, output: copy.deepcopy(prepared))
    monkeypatch.setattr(cli, "read_sealed", lambda path: copy.deepcopy(snapshot))

    def slow_prepare(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append("local")
        assert kwargs["calibration_timestamp"] == timestamp
        clock[0] += timedelta(minutes=6)
        if fail_noise:
            raise ValueError("Local channel diagnostic, no credential problem")
        return {"status": "LOCAL_READY"}

    monkeypatch.setattr(cli, "prepare", slow_prepare)
    records = {row["circuit_id"]: row for row in prepared["records"]}

    def loaded(path: Path, *, expected_sha256: str) -> QuantumCircuit:
        assert expected_sha256 == "a" * 64
        width = records[path.stem]["num_bits"]
        circuit = QuantumCircuit(16)
        circuit.add_register(ClassicalRegister(width, "m2"))
        circuit.x(prepared["physical_subset"][0])
        for bit, physical in enumerate(prepared["physical_subset"][:width]):
            circuit.measure(physical, bit)
        return circuit

    monkeypatch.setattr(cli, "load_qpy", loaded)
    output = tmp_path / "results/provider-fresh"
    args = argparse.Namespace(usage_estimates=None)
    result = cli._provider_preflight(args, tmp_path, output)
    if fail_noise:
        assert calls == ["account", "local"]
        assert result["diagnostic_kind"] == "LOCAL_CHANNEL_OR_SIMULATION_ERROR"
        assert result["provider_verified"] is True
        assert not (output / "local_usage_model.json").exists()
    else:
        assert calls == ["account", "local", "account", "plan"]
        assert result["obtained_at"] == clock[0].isoformat()
        assert result["calibration_timestamp"] == timestamp
        assert result["diagnostic_kind"] == "USAGE_LIMIT_EXCEEDED"
        assert read_json(output / "local_usage_model.json")["status"] == "BLOCKED"
        assert read_json(output / "provider_limit_assessment.json")["status"] == "PASS"
    assert result["status"] == "BLOCKED"
    assert result["hardware_used"] is False
    assert not (output / "jobs/ledger.json").exists()
