"""Explicit IBM reads and a pinned, single-job Sampler V2 boundary.

Importing this module never imports the Runtime SDK or discovers accounts.  Only
explicit preflight/submit/collect calls may construct an authenticated service.
The mock boundary is deliberately not evidence of IBM hardware readiness.
"""

from __future__ import annotations

import hashlib
import importlib
import logging
import math
import os
import re
import warnings
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Protocol

RUNTIME_VERSION = "0.47.0"
MAX_PREFLIGHT_AGE_SECONDS = 300


class SafetyError(ValueError):
    """A required safety or artifact contract is not satisfied."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        raise SafetyError("A valid explicit UTC timestamp is required") from None
    if parsed.tzinfo is None:
        raise SafetyError("Timestamp must include a timezone")
    return parsed.astimezone(UTC)


def instance_binding(instance: str) -> str:
    if not instance or not instance.strip():
        raise SafetyError("An explicit account instance is required")
    return hashlib.sha256(("m5-instance-binding-v1:" + instance).encode()).hexdigest()


_SECRET_KEY = re.compile(
    r"token|api.?key|authorization|password|secret|credential|(^|_)crn$|^instance$", re.I
)
_CRN = re.compile(r"crn:v\d+:[^\s\"'<>]+", re.I)
_AUTH = re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", re.I)
_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:token|api[_-]?key|authorization|password|secret)\b\s*[=:]\s*)"
    r"(?:[\"'][^\"']*[\"']|[^\s,;}]+)"
)


def redact(value: Any, *, secrets: Sequence[str] = ()) -> Any:
    """Recursively redact structured secrets and common text/header forms."""
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]"
            if _SECRET_KEY.search(str(key))
            else redact(item, secrets=secrets)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [redact(item, secrets=secrets) for item in value]
    if isinstance(value, str):
        for secret in sorted((item for item in secrets if item), key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        value = _CRN.sub("[REDACTED_INSTANCE]", value)
        value = _AUTH.sub("[REDACTED_AUTHORIZATION]", value)
        return _ASSIGNMENT.sub(lambda match: match[1] + "[REDACTED]", value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return redact(str(value), secrets=secrets)


def safe_runtime_options(
    max_execution_time: int = 40, *, rep_delay: float | None = None
) -> dict[str, Any]:
    """Only supported Sampler options; no Estimator resilience or sampler seed.

    max_execution_time limits QPU usage seconds, not queue or client wall time.
    Normal per-shot initialization remains enabled. An omitted repetition delay
    uses the documented backend default and must be described in the preflight.
    """
    if type(max_execution_time) is not int or not 1 <= max_execution_time <= 40:
        raise SafetyError("QPU execution cap must be between 1 and 40 seconds")
    execution: dict[str, Any] = {"init_qubits": True, "meas_type": "classified"}
    if rep_delay is not None:
        if isinstance(rep_delay, bool) or not math.isfinite(rep_delay) or rep_delay < 0:
            raise SafetyError("Repetition delay must be finite and nonnegative")
        execution["rep_delay"] = rep_delay
    return {
        "max_execution_time": max_execution_time,
        "dynamical_decoupling": {"enable": False},
        "twirling": {"enable_gates": False, "enable_measure": False},
        "execution": execution,
    }


def validate_runtime_options(options: Mapping[str, Any]) -> None:
    """Reject unknown keys and implicit optional processing."""
    execution = options.get("execution")
    if not isinstance(execution, dict):
        raise SafetyError("Explicit Sampler execution options are required")
    expected = safe_runtime_options(
        options.get("max_execution_time", 0), rep_delay=execution.get("rep_delay")
    )
    if dict(options) != expected:
        raise SafetyError("Unknown or non-approved Sampler options")


def sdk_option_contract(options: Mapping[str, Any]) -> dict[str, Any]:
    """Validate real pinned SDK option construction entirely offline."""
    validate_runtime_options(options)
    try:
        installed = version("qiskit-ibm-runtime")
    except PackageNotFoundError:
        raise SafetyError("Pinned Runtime SDK is not installed") from None
    if installed != RUNTIME_VERSION:
        raise SafetyError("Runtime SDK version differs from the reviewed pin")
    module = importlib.import_module("qiskit_ibm_runtime.options")
    result = module.SamplerOptions(**dict(options))
    for key in ("max_execution_time",):
        if getattr(result, key) != options[key]:
            raise SafetyError("Runtime SDK changed a requested option")
    for group in ("execution", "twirling", "dynamical_decoupling"):
        for key, value in options[group].items():
            if getattr(getattr(result, group), key) != value:
                raise SafetyError("Runtime SDK changed a requested option")
    return {"status": "PASS", "runtime_version": installed, "hardware_used": False}


@dataclass(frozen=True)
class LocalProviderConfig:
    """Private configuration; actual instance and token never enter public artifacts."""

    instance: str = field(repr=False)
    backend: str
    account_name: str | None = field(default=None, repr=False)
    token: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.instance.startswith("crn:") or not self.backend.strip():
            raise SafetyError("Explicit instance CRN and backend are required")
        if not self.account_name and not self.token:
            raise SafetyError("Explicit named account or local token environment is required")
        if self.account_name and self.token:
            raise SafetyError("Choose exactly one credential source")

    @property
    def binding(self) -> str:
        return instance_binding(self.instance)

    @classmethod
    def from_environment(cls) -> LocalProviderConfig:
        return cls(
            instance=os.environ.get("KM_QUANTUM_IBM_INSTANCE", ""),
            backend=os.environ.get("KM_QUANTUM_IBM_BACKEND", ""),
            account_name=os.environ.get("KM_QUANTUM_IBM_ACCOUNT_NAME") or None,
            token=os.environ.get("KM_QUANTUM_IBM_TOKEN") or None,
        )

    @classmethod
    def from_file(cls, path: str | Path) -> LocalProviderConfig:
        import json

        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if set(data) - {"instance", "backend", "account_name"}:
            raise SafetyError("Local configuration stores binding, not tokens or account dumps")
        return cls(
            instance=data.get("instance", ""),
            backend=data.get("backend", ""),
            account_name=data.get("account_name"),
        )


class ProviderAdapter(Protocol):
    source_kind: str
    single_job_contract: bool

    def metadata(
        self, config: LocalProviderConfig, plan: Mapping[str, Any] | None
    ) -> dict[str, Any]: ...

    def submit(
        self, pubs: Sequence[Any], options: Mapping[str, Any], tags: Sequence[str]
    ) -> Any: ...

    def job(self, provider_job_id: str) -> Any: ...


def _known_nonnegative(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def validate_preflight(
    evidence: Mapping[str, Any], plan: Mapping[str, Any] | None = None, *, now: str | None = None
) -> None:
    """A fresh verified free instance, real operational QPU, and complete limits."""
    if (
        evidence.get("provider_verified") is not True
        or evidence.get("source_kind") != "ibm_hardware"
        or evidence.get("plan") != "open"
        or evidence.get("pricing_type") != "free"
        or evidence.get("cost_eur") != 0
        or evidence.get("operational") is not True
        or evidence.get("simulator") is not False
        or evidence.get("fake") is not False
        or evidence.get("single_job_contract") is not True
        or evidence.get("runtime_version") != RUNTIME_VERSION
    ):
        raise SafetyError("Verified free Open Plan and operational real IBM QPU are required")
    current = parse_time(now or utc_now())
    age = (current - parse_time(evidence.get("obtained_at", ""))).total_seconds()
    if age < 0 or age > MAX_PREFLIGHT_AGE_SECONDS:
        raise SafetyError("Provider preflight is stale or future dated")
    usage = evidence.get("usage", {})
    limits = evidence.get("limits", {})
    for key in ("remaining_seconds", "consumed_seconds", "limit_seconds"):
        if not _known_nonnegative(usage.get(key)):
            raise SafetyError("Current usage and quota must be known")
    usage_age = (current - parse_time(usage.get("observed_at", ""))).total_seconds()
    if usage_age < 0 or usage_age > MAX_PREFLIGHT_AGE_SECONDS:
        raise SafetyError("Usage observation is stale or future dated")
    for key in ("max_shots", "max_jobs", "max_pubs"):
        if type(limits.get(key)) is not int or limits[key] < 1:
            raise SafetyError("Provider shot, job and PUB limits must be known")
    if not evidence.get("backend") or not evidence.get("instance_binding"):
        raise SafetyError("Explicit provider binding is missing")
    if plan is None:
        return
    if any(evidence.get(key) != plan.get(key) for key in ("backend", "instance_binding")):
        raise SafetyError("Provider instance or backend differs from the execution plan")
    jobs = plan["jobs"]
    reserved = sum(job["max_execution_time"] for job in jobs)
    if reserved > usage["remaining_seconds"] or len(jobs) > limits["max_jobs"]:
        raise SafetyError("Remaining provider quota does not cover the full approved reservation")
    estimates = evidence.get("execution_estimates", {})
    for job in jobs:
        estimate = estimates.get(job["id"], {})
        seconds = estimate.get("seconds")
        if not _known_nonnegative(seconds) or not estimate.get("source"):
            raise SafetyError("Every job requires a documented QPU usage estimate")
        if seconds > job["max_execution_time"]:
            raise SafetyError("QPU usage estimate exceeds the approved per-job cap")
        if len(job["pubs"]) > limits["max_pubs"]:
            raise SafetyError("PUB count exceeds the verified provider limit")
        if any(pub["shots"] > limits["max_shots"] for pub in job["pubs"]):
            raise SafetyError("Shots exceed the verified provider limit")


def preflight(
    config: LocalProviderConfig,
    adapter: ProviderAdapter,
    plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Explicit read boundary; no dry-run job, polling or submission."""
    try:
        evidence = adapter.metadata(config, plan)
        evidence["instance_binding"] = config.binding
        evidence["backend"] = config.backend
        evidence["source_kind"] = adapter.source_kind
        evidence["single_job_contract"] = adapter.single_job_contract
        if adapter.source_kind != "ibm_hardware":
            evidence.update(status="OFFLINE_READY", provider_verified=False, hardware_used=False)
            return dict(redact(evidence, secrets=(config.token or "", config.instance)))
        validate_preflight(evidence, plan)
        evidence["status"] = "PROVIDER_PREFLIGHT_READY"
        evidence["hardware_used"] = False
        return dict(redact(evidence, secrets=(config.token or "", config.instance)))
    except Exception as error:
        return {
            "status": "OWNER_CONFIGURATION_REQUIRED",
            "provider_verified": False,
            "hardware_used": False,
            "backend": config.backend,
            "instance_binding": config.binding,
            "diagnostic": redact(str(error), secrets=(config.token or "", config.instance)),
        }


@contextmanager
def _quiet_sdk() -> Iterator[None]:
    """SDK may include account names/CRNs in logs; public diagnostics are redacted."""
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            yield
    finally:
        logging.disable(previous)


class IBMRuntimeAdapter:
    """Reviewed qiskit-ibm-runtime 0.47.0 SamplerV2 in job mode only."""

    source_kind = "ibm_hardware"
    single_job_contract = True
    hardware_used = True

    def __init__(self, config: LocalProviderConfig) -> None:
        self.config = config
        self._service: Any = None
        self._backend: Any = None

    def _connect(self) -> None:
        if self._service is not None:
            return
        try:
            if version("qiskit-ibm-runtime") != RUNTIME_VERSION:
                raise SafetyError("Runtime version differs from the reviewed pin")
            sdk = importlib.import_module("qiskit_ibm_runtime")
            kwargs: dict[str, Any] = {"instance": self.config.instance}
            if self.config.account_name:
                kwargs["name"] = self.config.account_name
            else:
                kwargs.update(channel="ibm_quantum_platform", token=self.config.token)
            with _quiet_sdk():
                service = sdk.QiskitRuntimeService(**kwargs)
                backend = service.backend(self.config.backend, instance=self.config.instance)
            if service.channel not in {"ibm_quantum_platform", "ibm_cloud"}:
                raise SafetyError("Local Runtime channel is not hardware")
            backend_module = importlib.import_module("qiskit_ibm_runtime.ibm_backend")
            if not isinstance(backend, backend_module.IBMBackend):
                raise SafetyError("Fake/simulator backends are not hardware")
            if service.active_instance() != self.config.instance:
                raise SafetyError("Active provider instance differs from explicit binding")
            self._service, self._backend = service, backend
        except Exception as error:
            raise SafetyError(
                redact(str(error), secrets=(self.config.token or "", self.config.instance))
            ) from None

    def _disable_runtime_retries(self) -> None:
        """Pinned SDK defaults include POST retries; prevent duplicate requests.

        This narrow private transport contract is audited against 0.47.0 and
        tested with its actual RetrySession. Unknown SDK internals fail closed.
        Client read timeout is independent of the 40-second QPU usage cap.
        """
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        client = getattr(self._service, "_active_api_client", None)
        session = getattr(client, "_session", None)
        if session is None or not hasattr(session, "adapters"):
            raise SafetyError("Cannot prove that Runtime HTTP submission retries are disabled")
        clients = [client, *getattr(self._service, "_api_clients", {}).values()]
        backend_client = getattr(self._backend, "_api_client", None)
        if backend_client is not None:
            clients.append(backend_client)
        for item in clients:
            transport = getattr(item, "_session", None)
            if transport is None:
                raise SafetyError("Unknown Runtime transport; submission is blocked")
            for prefix in ("http://", "https://"):
                mounted = transport.adapters.get(prefix)
                if not isinstance(mounted, HTTPAdapter):
                    raise SafetyError("Unknown Runtime HTTP adapter; submission is blocked")
                mounted.max_retries = Retry(
                    total=0,
                    connect=0,
                    read=0,
                    redirect=0,
                    status=0,
                    other=0,
                    status_forcelist=(),
                    respect_retry_after_header=False,
                )
            transport.max_redirects = 0
            transport._timeout = (5.0, 30.0)

    def metadata(
        self, config: LocalProviderConfig, plan: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        if config.binding != self.config.binding or config.backend != self.config.backend:
            raise SafetyError("Adapter configuration differs from explicit request")
        self._connect()
        with _quiet_sdk():
            instances = self._service.instances()
            selected = [item for item in instances if item.get("crn") == config.instance]
            usage = self._service.usage()
            configuration = self._backend.configuration()
            status = self._backend.status()
            properties = self._backend.properties()
        if len(selected) != 1 or self._service.active_instance() != config.instance:
            raise SafetyError("The explicit account instance cannot be verified")
        now = utc_now()
        limit = usage.get("usage_limit_seconds", usage.get("usage_allocation_seconds"))
        consumed = usage.get("usage_consumed_seconds")
        remaining = usage.get("usage_remaining_seconds")
        pricing = selected[0].get("pricing_type")
        result: dict[str, Any] = {
            "provider_verified": True,
            "source_kind": self.source_kind,
            "backend": config.backend,
            "instance_binding": config.binding,
            "plan": selected[0].get("plan"),
            "pricing_type": pricing,
            "cost_eur": 0 if pricing == "free" else None,
            "operational": status.operational,
            "simulator": configuration.simulator,
            "fake": False,
            "runtime_version": RUNTIME_VERSION,
            "single_job_contract": True,
            "obtained_at": now,
            "calibration_timestamp": (
                properties.last_update_date.isoformat()
                if properties is not None and getattr(properties, "last_update_date", None)
                else None
            ),
            "usage": {
                "remaining_seconds": remaining,
                "consumed_seconds": consumed,
                "limit_seconds": limit,
                "observed_at": now,
            },
            "limits": {
                "max_shots": getattr(configuration, "max_shots", None),
                "max_pubs": getattr(configuration, "max_experiments", None),
                "max_jobs": getattr(configuration, "max_jobs", None),
            },
            "rep_delay": {
                "supported": getattr(configuration, "dynamic_reprate_enabled", False),
                "value": getattr(configuration, "default_rep_delay", None),
                "range": getattr(configuration, "rep_delay_range", None),
                "source": "backend.configuration.default_rep_delay",
            },
            "execution_estimates": {},
        }
        if plan is not None:
            result["execution_estimates"] = {
                job["id"]: {
                    "seconds": job.get("estimated_qpu_seconds"),
                    "source": job.get("estimate_source"),
                }
                for job in plan["jobs"]
            }
        # Missing service facts are kept unknown. No inferred paid fallback or
        # fabricated job capacity converts this into READY_FOR_APPROVAL.
        return result

    def submit(self, pubs: Sequence[Any], options: Mapping[str, Any], tags: Sequence[str]) -> Any:
        sdk_option_contract(options)
        self._connect()
        sdk = importlib.import_module("qiskit_ibm_runtime")
        contexts = importlib.import_module("qiskit_ibm_runtime.utils.default_session")
        if contexts.get_cm_session() is not None:
            raise SafetyError("Open Runtime Session or Batch context is forbidden")
        if self._service.active_instance() != self.config.instance:
            raise SafetyError("Provider instance changed before submission")
        self._disable_runtime_retries()
        runtime_options = dict(options)
        runtime_options["environment"] = {"job_tags": list(tags)}
        with _quiet_sdk():
            sampler = sdk.SamplerV2(mode=self._backend, options=runtime_options)
            if sampler.mode is not None:
                raise SafetyError("Sampler must use single-job mode")
            return sampler.run(pubs)

    def job(self, provider_job_id: str) -> Any:
        self._connect()
        if self._service.active_instance() != self.config.instance:
            raise SafetyError("Provider instance changed before collection")
        with _quiet_sdk():
            job = self._service.job(provider_job_id)
        if self._service.active_instance() != self.config.instance:
            raise SafetyError("Provider changed instance while collecting job")
        return job

    def verify_job_binding(self, job: Any) -> None:
        """Read returned job identity only after its ID has been persisted."""
        backend = job.backend()
        if backend.name != self.config.backend:
            raise SafetyError("Provider accepted a job on an unapproved backend")
        client = getattr(job, "_api_client", None)
        if getattr(client, "_instance", None) != self.config.instance:
            raise SafetyError("Returned provider job has an unapproved instance binding")
        if self._service.active_instance() != self.config.instance:
            raise SafetyError("Provider instance changed during job retrieval")

    def backend(self) -> Any:
        """Explicit preflight caller may read target/properties for its snapshot."""
        self._connect()
        return self._backend

    def backend_for_snapshot(self) -> Any:
        """Explicit read-only access for target and calibration snapshot creation."""
        return self.backend()
