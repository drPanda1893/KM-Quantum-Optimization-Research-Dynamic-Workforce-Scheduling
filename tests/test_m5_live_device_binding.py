"""Pure device binding and SDK refresh are offline, immutable and fail closed."""

from __future__ import annotations

import copy
import logging
import socket
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from km_quantum.m4.io import canonical_hash
from km_quantum.m5.approvals import plan_hash
from km_quantum.m5.device_binding import (
    DIAGNOSTIC_KIND,
    snapshot_with_calibration_timestamp,
    validate_device_binding,
)
from km_quantum.m5.provider import (
    IBMRuntimeAdapter,
    LocalProviderConfig,
    SafetyError,
    safe_runtime_options,
)

NOW = "2026-10-12T17:00:00+00:00"
OLD = "2026-10-12T16:00:00+00:00"
LIVE = "2026-10-12T16:55:00+00:00"


def _sealed(payload: dict[str, Any]) -> dict[str, Any]:
    payload = {key: value for key, value in payload.items() if key != "content_sha256"}
    return {**payload, "content_sha256": canonical_hash(payload)}


def _reseal_plan(plan: dict[str, Any]) -> None:
    plan["plan_hash"] = plan_hash(plan)


@pytest.fixture
def binding() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Explicit hypothetical provider facts; no account or hardware is accessed."""
    snapshot = {
        "schema_version": 2,
        "artifact_type": "m5_backend_snapshot",
        "source": "provider",
        "hardware_used": False,
        "credentials_exported": False,
        "backend_name": "synthetic-device-binding-only",
        "backend_width": 3,
        "physical_subset": [0, 2],
        "operation_names": ["delay", "measure", "reset", "x"],
        "instructions": [],
        "qubits": [
            {
                "physical_id": qubit,
                "t1_seconds": 100e-6,
                "t2_seconds": 80e-6,
                "frequency_hz": None,
                "readout_error": 0.01,
                "readout_matrix": [[0.99, 0.01], [0.01, 0.99]],
                "readout_matrix_source": "symmetric_approximation_from_target_mean_error",
                "missing": ["frequency_hz"],
            }
            for qubit in (0, 2)
        ],
        "target_timing": {
            "dt_seconds": 2e-10,
            "timing_constraints": {
                "granularity": 1,
                "min_length": 1,
                "pulse_alignment": 16,
                "acquire_alignment": 16,
            },
            "source": "qiskit.transpiler.Target.dt/timing_constraints()",
        },
        "retrieved_at": "2026-10-12T16:10:00+00:00",
        "calibration_timestamp": OLD,
        "missing": [],
        "readout_matrix_convention": "rows=true, columns=observed",
    }
    snapshot["instructions"] = [
        {"name": "x", "qubits": [0], "duration_seconds": 1e-6, "error": 0.001, "missing": []}
    ]
    for qubit in (0, 2):
        snapshot["instructions"].extend(
            [
                {
                    "name": "measure",
                    "qubits": [qubit],
                    "duration_seconds": 2e-6,
                    "error": 0.01,
                    "missing": [],
                },
                {
                    "name": "reset",
                    "qubits": [qubit],
                    "duration_seconds": 3e-6,
                    "error": None,
                    "missing": ["error"],
                },
                {
                    "name": "delay",
                    "qubits": [qubit],
                    "duration_seconds": None,
                    "error": None,
                    "missing": ["duration_seconds", "error"],
                },
            ]
        )
    frozen = _sealed(snapshot)
    current = _sealed(
        {**copy.deepcopy(snapshot), "retrieved_at": NOW, "calibration_timestamp": LIVE}
    )
    plan = {
        "artifact_type": "m5_hardware_execution_plan",
        "source_kind": "ibm_hardware",
        "provider_verified": True,
        "hardware_enabled": False,
        "submission_approved": False,
        "backend": snapshot["backend_name"],
        "instance_binding": "a" * 64,
        "layout": [0, 2],
        "target_snapshot_sha256": frozen["content_sha256"],
        "runtime_options": safe_runtime_options(rep_delay=0.00025),
        "provider_limit_assessment": {
            "jobs": {"smoke": {"pubs": [{"native_gate_counts": {"x": 1, "measure": 2}}]}}
        },
        "local_usage_model": {
            "target_snapshot_sha256": frozen["content_sha256"],
            "jobs": [
                {
                    "job_id": "smoke",
                    "rep_delay": {
                        "seconds": 0.00025,
                        "source": "synthetic explicit backend default",
                    },
                }
            ],
        },
    }
    _reseal_plan(plan)
    evidence = {
        "provider_verified": True,
        "source_kind": "ibm_hardware",
        "backend": plan["backend"],
        "instance_binding": plan["instance_binding"],
        "obtained_at": NOW,
        "calibration_timestamp": LIVE,
        "rep_delay": {
            "supported": True,
            "value": 0.00025,
            "range": [0, 0.001],
            "source": "backend.configuration.default_rep_delay",
        },
    }
    return plan, frozen, current, evidence


def test_timestamp_advance_preserves_frozen_scientific_reference(binding: Any) -> None:
    before = copy.deepcopy(binding)
    result = validate_device_binding(*binding, now=NOW)
    assert result["status"] == "PASS" and result["calibration_timestamp_advanced"] is True
    assert result["frozen_scientific_reference_changed"] is False
    assert (
        result["frozen_calibration_timestamp"] == OLD
        and result["current_calibration_timestamp"] == LIVE
    )
    assert result["hardware_used"] is False and result["submission_approved"] is False
    assert result["provider_contacted"] is False
    assert result["content_sha256"] == canonical_hash(
        {key: value for key, value in result.items() if key != "content_sha256"}
    )
    assert any("frequency_hz" in key for key in result["unchanged_unknown_fields"])
    assert binding == before


@pytest.mark.parametrize(
    "change",
    [
        "gate-duration",
        "gate-error",
        "t1",
        "t2",
        "readout",
        "width",
        "mapping",
        "operation",
        "dt",
        "alignment",
        "matrix-convention",
        "source",
    ],
)
def test_changed_resealed_parameter_facts_block(binding: Any, change: str) -> None:
    plan, frozen, current, evidence = binding
    if change == "gate-duration":
        current["instructions"][0]["duration_seconds"] += 1e-15
    elif change == "gate-error":
        current["instructions"][0]["error"] += 1e-15
    elif change in {"t1", "t2"}:
        current["qubits"][0][change + "_seconds"] += 1e-15
    elif change == "readout":
        current["qubits"][0]["readout_error"] += 1e-15
    elif change == "width":
        current["backend_width"] += 1
    elif change == "mapping":
        current["physical_subset"].reverse()
    elif change == "operation":
        current["operation_names"].append("sx")
    elif change == "dt":
        current["target_timing"]["dt_seconds"] *= 2
    elif change == "alignment":
        current["target_timing"]["timing_constraints"]["acquire_alignment"] *= 2
    elif change == "matrix-convention":
        current["readout_matrix_convention"] = "rows=observed"
    else:
        current["source"] = "offline_synthetic"
    current = _sealed(current)
    with pytest.raises(SafetyError) as caught:
        validate_device_binding(plan, frozen, current, evidence, now=NOW)
    assert caught.value.diagnostic_kind == DIAGNOSTIC_KIND


@pytest.mark.parametrize(
    "value",
    [
        None,
        "2026-10-12T16:55:00",
        "invalid",
        "2026-10-12T18:00:00+00:00",
        "2026-10-12T15:59:59+00:00",
    ],
)
def test_missing_malformed_future_or_older_calibration_blocks(binding: Any, value: Any) -> None:
    plan, frozen, current, evidence = binding
    current["calibration_timestamp"] = value
    evidence["calibration_timestamp"] = value
    with pytest.raises(SafetyError) as caught:
        validate_device_binding(plan, frozen, _sealed(current), evidence, now=NOW)
    assert caught.value.diagnostic_kind == DIAGNOSTIC_KIND


@pytest.mark.parametrize(
    "change",
    [
        "current-stale",
        "provider-stale",
        "current-future",
        "sdk-timestamp",
        "instance",
        "backend",
        "plan-hash",
        "target-hash",
        "snapshot-seal",
    ],
)
def test_freshness_and_hash_binding_fail_closed(binding: Any, change: str) -> None:
    plan, frozen, current, evidence = binding
    if change == "current-stale":
        current["retrieved_at"] = "2026-10-12T16:54:59+00:00"
    elif change == "provider-stale":
        evidence["obtained_at"] = "2026-10-12T16:54:59+00:00"
    elif change == "current-future":
        current["retrieved_at"] = "2026-10-12T17:00:01+00:00"
    elif change == "sdk-timestamp":
        evidence["calibration_timestamp"] = OLD
    elif change in {"instance", "backend"}:
        evidence["instance_binding" if change == "instance" else "backend"] = "changed"
    elif change == "plan-hash":
        plan["plan_hash"] = "b" * 64
    elif change == "target-hash":
        plan["target_snapshot_sha256"] = "b" * 64
        _reseal_plan(plan)
    else:
        current["content_sha256"] = "b" * 64
    current = current if change == "snapshot-seal" else _sealed(current)
    with pytest.raises(SafetyError):
        validate_device_binding(plan, frozen, current, evidence, now=NOW)


@pytest.mark.parametrize(
    "change",
    [
        "legacy-no-timing",
        "dt-unknown",
        "duration-unknown",
        "bad-alignment",
        "bad-t1",
        "nan-error",
        "duplicate-instruction",
    ],
)
def test_required_unknown_or_invalid_device_data_is_not_guessed(binding: Any, change: str) -> None:
    plan, frozen, current, evidence = binding
    for snapshot in (frozen, current):
        if change == "legacy-no-timing":
            snapshot.pop("target_timing")
            snapshot["schema_version"] = 1
        elif change == "dt-unknown":
            snapshot["target_timing"]["dt_seconds"] = None
        elif change == "duration-unknown":
            snapshot["instructions"][0]["duration_seconds"] = None
            snapshot["instructions"][0]["missing"] = ["duration_seconds"]
        elif change == "bad-alignment":
            snapshot["target_timing"]["timing_constraints"]["pulse_alignment"] = True
        elif change == "bad-t1":
            snapshot["qubits"][0]["t1_seconds"] = -1
        elif change == "nan-error":
            snapshot["instructions"][0]["error"] = "nan"
        else:
            snapshot["instructions"].append(copy.deepcopy(snapshot["instructions"][0]))
    frozen, current = _sealed(frozen), _sealed(current)
    plan["target_snapshot_sha256"] = frozen["content_sha256"]
    plan["local_usage_model"]["target_snapshot_sha256"] = frozen["content_sha256"]
    _reseal_plan(plan)
    with pytest.raises(SafetyError):
        validate_device_binding(plan, frozen, current, evidence, now=NOW)


@pytest.mark.parametrize(
    "change",
    [
        "unsupported",
        "range-missing",
        "range-excludes",
        "range-inverted",
        "value-bool",
        "model-basis",
    ],
)
def test_repetition_delay_must_remain_supported_and_bound(binding: Any, change: str) -> None:
    plan, frozen, current, evidence = binding
    if change == "unsupported":
        evidence["rep_delay"]["supported"] = False
    elif change == "range-missing":
        evidence["rep_delay"]["range"] = None
    elif change == "range-excludes":
        evidence["rep_delay"]["range"] = [0.0003, 0.001]
    elif change == "range-inverted":
        evidence["rep_delay"]["range"] = [0.001, 0]
    elif change == "value-bool":
        plan["runtime_options"]["execution"]["rep_delay"] = False
        _reseal_plan(plan)
    else:
        plan["local_usage_model"]["jobs"][0]["rep_delay"]["seconds"] = 0.0005
        _reseal_plan(plan)
    with pytest.raises(SafetyError):
        validate_device_binding(plan, frozen, current, evidence, now=NOW)


def test_explicit_delay_ignores_a_new_default_but_implicit_default_cannot_drift(
    binding: Any,
) -> None:
    plan, frozen, current, evidence = binding
    evidence["rep_delay"]["value"] = 0.0004
    assert (
        validate_device_binding(plan, frozen, current, evidence, now=NOW)["rep_delay_seconds"]
        == 0.00025
    )
    plan["runtime_options"]["execution"].pop("rep_delay")
    _reseal_plan(plan)
    with pytest.raises(SafetyError, match="usage basis"):
        validate_device_binding(plan, frozen, current, evidence, now=NOW)
    evidence["rep_delay"]["value"] = 0.00025
    assert (
        validate_device_binding(plan, frozen, current, evidence, now=NOW)["rep_delay_mode"]
        == "frozen_backend_default"
    )


def test_timestamp_bridge_is_sealed_explicit_and_does_not_mutate(binding: Any) -> None:
    raw = copy.deepcopy(binding[2])
    raw.pop("content_sha256")
    raw["calibration_timestamp"] = None
    raw["missing"] = ["calibration_timestamp"]
    before = copy.deepcopy(raw)
    result = snapshot_with_calibration_timestamp(raw, LIVE)
    assert result["calibration_timestamp"] == LIVE and result["retrieved_at"] == NOW
    assert result["missing"] == [] and raw == before
    assert result["content_sha256"] == canonical_hash(
        {key: value for key, value in result.items() if key != "content_sha256"}
    )
    with pytest.raises(SafetyError):
        snapshot_with_calibration_timestamp(result, OLD)


def _adapter() -> IBMRuntimeAdapter:
    return IBMRuntimeAdapter(
        LocalProviderConfig(
            instance="crn:v1:offline-unit-test", backend="test", token="synthetic-secret"
        )
    )


def test_refresh_is_exactly_once_quiet_and_never_submits(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter()
    events = []
    previous = logging.root.manager.disable

    def refresh() -> None:
        assert logging.root.manager.disable == logging.CRITICAL
        events.append("refresh")

    adapter._backend = SimpleNamespace(refresh=refresh)
    adapter._service = SimpleNamespace(active_instance=lambda: adapter.config.instance)
    monkeypatch.setattr(adapter, "_connect", lambda: events.append("explicit-connect-boundary"))
    monkeypatch.setattr(adapter, "submit", lambda *args: pytest.fail("No job is allowed"))
    monkeypatch.setattr(
        socket.socket, "connect", lambda *args: pytest.fail("No network is allowed")
    )
    assert adapter.refresh_backend_for_snapshot() is adapter._backend
    assert events == ["explicit-connect-boundary", "refresh"]
    assert logging.root.manager.disable == previous


def test_fresh_snapshot_refreshes_once_and_bridges_cached_sdk_timestamp(
    binding: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import km_quantum.m5.provider as provider_module
    import km_quantum.m5.snapshots as snapshots

    adapter = _adapter()
    calls = []
    adapter._backend = SimpleNamespace(
        refresh=lambda: calls.append("refresh"),
        properties=lambda: SimpleNamespace(last_update_date=datetime.fromisoformat(LIVE)),
    )
    adapter._service = SimpleNamespace(active_instance=lambda: adapter.config.instance)
    monkeypatch.setattr(adapter, "_connect", lambda: None)
    monkeypatch.setattr(provider_module, "utc_now", lambda: NOW)
    raw = copy.deepcopy(binding[2])
    raw.pop("content_sha256")
    raw["calibration_timestamp"] = None
    raw["missing"] = ["calibration_timestamp"]

    def export(backend: Any, subset: Any, *, retrieved_at: str) -> dict[str, Any]:
        assert subset == [0, 2] and retrieved_at == NOW
        assert logging.root.manager.disable == logging.CRITICAL
        calls.append("snapshot-export")
        return raw

    monkeypatch.setattr(snapshots, "snapshot_backend", export)
    assert adapter.fresh_device_snapshot([0, 2])["calibration_timestamp"] == LIVE
    assert calls == ["refresh", "snapshot-export"]


@pytest.mark.parametrize("timestamp", [None, datetime(2026, 10, 12), "2026-10-12T16:55:00+00:00"])
def test_sdk_timestamp_must_be_a_known_timezone_aware_datetime(
    timestamp: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _adapter()
    adapter._backend = SimpleNamespace(
        refresh=lambda: None, properties=lambda: SimpleNamespace(last_update_date=timestamp)
    )
    adapter._service = SimpleNamespace(active_instance=lambda: adapter.config.instance)
    monkeypatch.setattr(adapter, "_connect", lambda: None)
    with pytest.raises(SafetyError) as caught:
        adapter.fresh_device_snapshot([0, 2])
    assert caught.value.diagnostic_kind == DIAGNOSTIC_KIND


def test_refresh_errors_redact_loaded_named_token_and_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter()
    secret = "opaque-named-secret"

    def fail() -> None:
        raise RuntimeError(
            f"Refresh {secret} {adapter.config.instance} Authorization: Bearer private"
        )

    adapter._backend = SimpleNamespace(refresh=fail)
    adapter._service = SimpleNamespace(_account=SimpleNamespace(token=secret))
    monkeypatch.setattr(adapter, "_connect", lambda: None)
    with pytest.raises(SafetyError) as caught:
        adapter.refresh_backend_for_snapshot()
    assert secret not in str(caught.value) and adapter.config.instance not in str(caught.value)
    assert "Bearer private" not in str(caught.value)


def test_actual_pinned_sdk_refresh_abi_is_readonly(monkeypatch: pytest.MonkeyPatch) -> None:
    import qiskit_ibm_runtime.ibm_backend as backend_module
    from qiskit_ibm_runtime.fake_provider import FakeManilaV2
    from qiskit_ibm_runtime.ibm_backend import IBMBackend

    adapter = _adapter()
    calls = []
    config = FakeManilaV2().configuration()

    def configuration(name: str, *, refresh: bool, calibration_id: Any) -> dict[str, Any]:
        assert refresh is True and logging.root.manager.disable == logging.CRITICAL
        calls.append("configuration-refresh")
        return {}

    service = SimpleNamespace(
        _get_api_client=lambda instance: SimpleNamespace(backend_configuration=configuration),
        active_instance=lambda: adapter.config.instance,
    )
    backend = IBMBackend(config, service, None, instance=adapter.config.instance)
    monkeypatch.setattr(backend_module, "configuration_from_server_data", lambda **kwargs: None)
    monkeypatch.setattr(
        backend, "properties", lambda *, refresh: calls.append(("properties", refresh))
    )
    monkeypatch.setattr(
        backend, "_convert_to_target", lambda *, refresh: calls.append(("target", refresh))
    )
    monkeypatch.setattr(
        socket.socket, "connect", lambda *args: pytest.fail("No network is allowed")
    )
    adapter._backend, adapter._service = backend, service
    monkeypatch.setattr(adapter, "_connect", lambda: None)
    assert adapter.refresh_backend_for_snapshot() is backend
    assert calls == ["configuration-refresh", ("properties", True), ("target", True)]


@pytest.mark.parametrize("change", ["plan-nan", "malformed-model", "huge-dt"])
def test_noncanonical_or_malformed_json_has_typed_device_diagnostics(
    binding: Any, change: str
) -> None:
    plan, frozen, current, evidence = binding
    if change == "plan-nan":
        plan["unsupported"] = float("nan")
    elif change == "malformed-model":
        plan["local_usage_model"] = None
        _reseal_plan(plan)
    else:
        current["target_timing"]["dt_seconds"] = 10**1000
        current = _sealed(current)
    with pytest.raises(SafetyError) as caught:
        validate_device_binding(plan, frozen, current, evidence, now=NOW)
    assert caught.value.diagnostic_kind == DIAGNOSTIC_KIND
