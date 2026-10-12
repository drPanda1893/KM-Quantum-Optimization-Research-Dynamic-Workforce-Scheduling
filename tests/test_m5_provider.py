"""No provider network in tests; actual SDK options are constructed offline."""

import copy
import importlib
import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from km_quantum.m5.provider import (
    IBMRuntimeAdapter,
    LocalProviderConfig,
    SafetyError,
    instance_binding,
    preflight,
    redact,
    safe_runtime_options,
    sdk_option_contract,
    validate_runtime_options,
)


@pytest.fixture(autouse=True)
def offline_sdk_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """No account access or HTTP, and no Windows WMI probe during SDK import."""
    import requests

    def deny_request(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Provider tests may never perform HTTP requests")

    monkeypatch.setattr(requests.sessions.Session, "request", deny_request)
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "release", lambda: "synthetic-test-release")


def test_recursive_redaction_covers_tokens_headers_crn_and_explicit_secret() -> None:
    token = "do-not-leak-test-token"
    instance = "crn:v1:bluemix:public:quantum:eu-de:a:test:instance::"
    value = {
        "nested": {"api_key": token, "Authorization": "Bearer abc123", "instance": instance},
        "text": f"URL {instance}, token={token}; Authorization: Bearer abc123",
        "raw": token,
        "instance_binding": instance_binding(instance),
    }
    result = redact(value, secrets=(token,))
    serialized = json.dumps(result)
    assert token not in serialized
    assert "abc123" not in serialized
    assert instance not in serialized
    assert result["instance_binding"] == instance_binding(instance)


def test_options_disable_processing_but_keep_documented_initialization() -> None:
    options = safe_runtime_options(rep_delay=0.00025)
    assert options["dynamical_decoupling"]["enable"] is False
    assert options["twirling"] == {"enable_gates": False, "enable_measure": False}
    assert options["execution"] == {
        "init_qubits": True,
        "meas_type": "classified",
        "rep_delay": 0.00025,
    }
    assert options["max_execution_time"] == 40
    validate_runtime_options(options)


@pytest.mark.parametrize("unknown", ["resilience_level", "seed", "experimental", "environment"])
def test_unrecognized_or_estimator_options_are_rejected(unknown: str) -> None:
    options = safe_runtime_options()
    options[unknown] = 0
    with pytest.raises(SafetyError, match="Unknown"):
        validate_runtime_options(options)


@pytest.mark.parametrize("value", [False, 0, -1, 41, 40.0])
def test_cap_is_exact_positive_integer_bounded_by_40(value: Any) -> None:
    with pytest.raises(SafetyError):
        safe_runtime_options(value)


def test_pinned_sdk_option_construction_does_not_create_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = pytest.importorskip("qiskit_ibm_runtime")

    def never_connect(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Offline option test must not discover accounts or create a service")

    monkeypatch.setattr(sdk, "QiskitRuntimeService", never_connect)
    assert sdk_option_contract(safe_runtime_options(rep_delay=0.00025)) == {
        "status": "PASS",
        "runtime_version": "0.47.0",
        "hardware_used": False,
    }


def test_incompatible_sdk_pin_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = importlib.import_module("km_quantum.m5.provider")
    monkeypatch.setattr(provider, "version", lambda name: "0.50.0")
    with pytest.raises(SafetyError, match="pin"):
        sdk_option_contract(safe_runtime_options())


def test_private_config_requires_instance_backend_and_explicit_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (
        "KM_QUANTUM_IBM_INSTANCE",
        "KM_QUANTUM_IBM_BACKEND",
        "KM_QUANTUM_IBM_TOKEN",
        "KM_QUANTUM_IBM_ACCOUNT_NAME",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("QISKIT_IBM_TOKEN", "unrelated-sdk-default")
    with pytest.raises(SafetyError):
        LocalProviderConfig.from_environment()
    with pytest.raises(SafetyError):
        LocalProviderConfig(instance="crn:v1:test", backend="backend")
    config = LocalProviderConfig(instance="crn:v1:test", backend="backend", token="private-test")
    assert "private-test" not in repr(config)
    assert "crn:v1" not in repr(config)
    adapter = IBMRuntimeAdapter(config)
    assert adapter._service is None  # Construction is offline.


def test_file_config_does_not_accept_token_or_account_dump(tmp_path: Path) -> None:
    path = tmp_path / "private.json"
    path.write_text(json.dumps({"token": "secret", "instance": "crn:v1:test"}), encoding="utf-8")
    with pytest.raises(SafetyError, match="not tokens"):
        LocalProviderConfig.from_file(path)


def test_mock_preflight_can_never_claim_hardware_readiness() -> None:
    config = LocalProviderConfig(instance="crn:v1:test", backend="test", account_name="named")

    class MockAdapter:
        source_kind = "mock"
        single_job_contract = True

        def metadata(self, config: Any, plan: Any) -> dict[str, Any]:
            return {"provider_verified": True, "status": "READY_FOR_APPROVAL"}

    evidence = preflight(config, MockAdapter())  # type: ignore[arg-type]
    assert evidence["status"] == "OFFLINE_READY"
    assert evidence["provider_verified"] is False
    assert evidence["hardware_used"] is False


def test_configuration_failure_is_redacted_and_never_falls_back() -> None:
    config = LocalProviderConfig(instance="crn:v1:test", backend="test", token="bare-secret")

    class FailingAdapter:
        source_kind = "ibm_hardware"
        single_job_contract = True

        def metadata(self, config: Any, plan: Any) -> dict[str, Any]:
            raise RuntimeError(f"failure bare-secret {config.instance}")

    evidence = preflight(config, FailingAdapter())  # type: ignore[arg-type]
    assert evidence["status"] == "OWNER_CONFIGURATION_REQUIRED"
    assert "bare-secret" not in json.dumps(evidence)
    assert "crn:v1" not in json.dumps(evidence)


def test_runtime_option_contract_checks_all_fields() -> None:
    options = copy.deepcopy(safe_runtime_options())
    options["execution"]["init_qubits"] = False
    with pytest.raises(SafetyError, match="Unknown"):
        validate_runtime_options(options)


def test_real_pinned_sampler_routes_one_call_to_one_service_job_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real SDK run/option serialization, with only the service transport replaced."""
    from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister
    from qiskit_ibm_runtime import QiskitRuntimeService
    from qiskit_ibm_runtime.api.session import RetrySession
    from qiskit_ibm_runtime.fake_provider import FakeManilaV2
    from qiskit_ibm_runtime.ibm_backend import IBMBackend

    config = LocalProviderConfig(
        instance="crn:v1:synthetic-contract", backend="synthetic-contract", token="mock-secret"
    )
    fake = FakeManilaV2()
    # No Service constructor/account read, and no API client is instantiated.
    service = object.__new__(QiskitRuntimeService)
    transport = RetrySession("https://example.invalid")
    service._active_api_client = SimpleNamespace(_session=transport)
    backend = IBMBackend(fake.configuration(), service, None, instance=config.instance)
    backend._target = fake.target
    monkeypatch.setattr(backend, "properties", lambda **kwargs: None)
    monkeypatch.setattr(service, "active_instance", lambda: config.instance)
    calls: list[dict[str, Any]] = []
    sentinel = object()

    def offline_transport(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return sentinel

    monkeypatch.setattr(service, "_run", offline_transport)
    adapter = IBMRuntimeAdapter(config)
    adapter._service, adapter._backend = service, backend
    qreg, creg = QuantumRegister(5, "q"), ClassicalRegister(2, "m2")
    circuit = QuantumCircuit(qreg, creg)
    circuit.x(0)
    circuit.measure([0, 1], creg)
    pubs = [(circuit, None, 1024)] * 24
    assert adapter.submit(pubs, safe_runtime_options(), ["synthetic-plan"]) is sentinel
    assert len(calls) == 1
    for mounted in transport.adapters.values():
        assert mounted.max_retries.total == 0
        assert mounted.max_retries.connect == 0
        assert mounted.max_retries.read == 0
        assert mounted.max_retries.status == 0
        assert mounted.max_retries.redirect == 0
    assert transport.max_redirects == 0
    assert transport._timeout == (5.0, 30.0)
    assert calls[0]["program_id"] == "sampler"
    assert len(calls[0]["inputs"]["pubs"]) == 24
    assert calls[0]["options"]["max_execution_time"] == 40
    assert calls[0]["options"]["instance"] == config.instance
    execution = calls[0]["inputs"]["options"]["execution"]
    assert execution["init_qubits"] is True
    assert execution["meas_type"] == "classified"
    assert calls[0]["inputs"]["options"]["dynamical_decoupling"]["enable"] is False
    assert calls[0]["inputs"]["options"]["twirling"]["enable_measure"] is False


def test_open_session_context_blocks_adapter_before_sampler_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts = importlib.import_module("qiskit_ibm_runtime.utils.default_session")
    config = LocalProviderConfig(
        instance="crn:v1:synthetic-contract", backend="test", token="mock-secret"
    )
    adapter = IBMRuntimeAdapter(config)
    monkeypatch.setattr(adapter, "_connect", lambda: None)
    monkeypatch.setattr(contexts, "get_cm_session", lambda: object())
    with pytest.raises(SafetyError, match="Session or Batch"):
        adapter.submit([], safe_runtime_options(), [])


def test_unknown_sdk_transport_refuses_instead_of_allowing_implicit_retry() -> None:
    config = LocalProviderConfig(instance="crn:v1:test", backend="test", token="mock-secret")
    adapter = IBMRuntimeAdapter(config)
    adapter._service = SimpleNamespace()
    with pytest.raises(SafetyError, match="retries are disabled"):
        adapter._disable_runtime_retries()


def test_explicit_snapshot_read_and_calibration_timestamp_use_allowlisted_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = LocalProviderConfig(instance="crn:v1:test", backend="test", token="mock-secret")
    adapter = IBMRuntimeAdapter(config)
    timestamp = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)
    adapter._service = SimpleNamespace(
        instances=lambda: [{"crn": config.instance, "plan": "open", "pricing_type": "free"}],
        usage=lambda: {
            "usage_limit_seconds": 600,
            "usage_consumed_seconds": 1,
            "usage_remaining_seconds": 599,
        },
        active_instance=lambda: config.instance,
    )
    adapter._backend = SimpleNamespace(
        configuration=lambda: SimpleNamespace(
            simulator=False, max_shots=1024, max_experiments=24, max_jobs=3
        ),
        status=lambda: SimpleNamespace(operational=True),
        properties=lambda: SimpleNamespace(last_update_date=timestamp),
    )
    connect_calls = []
    monkeypatch.setattr(adapter, "_connect", lambda: connect_calls.append("explicit-read"))
    assert adapter.backend_for_snapshot() is adapter._backend
    metadata = adapter.metadata(config, None)
    assert metadata["calibration_timestamp"] == timestamp.isoformat()
    assert connect_calls == ["explicit-read", "explicit-read"]
    assert config.instance not in json.dumps(metadata)
    assert config.token not in json.dumps(metadata)
