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

from km_quantum.m5.budget_policy import (
    BUDGET_B_VERSION,
    BUDGET_V1_VERSION,
    JOB_IDS,
    BudgetPolicy,
    resolve_budget_policy,
    validate_budget_authorization,
)

RUNTIME_VERSION = "0.47.0"
MAX_PREFLIGHT_AGE_SECONDS = 300


class SafetyError(ValueError):
    """A required safety or artifact contract is not satisfied."""

    def __init__(
        self, message: str, *, diagnostic_kind: str = "OWNER_CONFIGURATION_REQUIRED"
    ) -> None:
        super().__init__(message)
        self.diagnostic_kind = diagnostic_kind


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
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if name == "budget_authorization":
                try:
                    public = validate_budget_authorization(item).model_dump(mode="json")
                except (ValueError, TypeError):
                    result[name] = "[REDACTED]"
                else:
                    result[name] = redact(public, secrets=secrets)
            elif (
                name == "budget_authorization_sha256"
                and isinstance(item, str)
                and re.fullmatch(r"[0-9a-f]{64}", item)
            ):
                result[name] = item
            else:
                result[name] = (
                    "[REDACTED]" if _SECRET_KEY.search(name) else redact(item, secrets=secrets)
                )
        return result
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
    max_execution_time: int = 40,
    *,
    rep_delay: float | None = None,
    budget_policy: BudgetPolicy | None = None,
) -> dict[str, Any]:
    """Only supported Sampler options; no Estimator resilience or sampler seed.

    max_execution_time limits QPU usage seconds, not queue or client wall time.
    Normal per-shot initialization remains enabled. An omitted repetition delay
    uses the documented backend default and must be described in the preflight.
    """
    policy = budget_policy or resolve_budget_policy()
    maximum = 80 if policy.version == BUDGET_B_VERSION else 40
    expected_caps = (
        {"smoke": 20, "main-block-1": 80, "main-block-2": 80}
        if policy.version == BUDGET_B_VERSION
        else {identity: 40 for identity in JOB_IDS}
    )
    if (
        policy.version not in {BUDGET_V1_VERSION, BUDGET_B_VERSION}
        or policy.per_job_qpu_seconds != expected_caps
        or policy.max_total_qpu_seconds != (180 if policy.version == BUDGET_B_VERSION else 120)
        or policy.max_qpu_seconds_per_job != maximum
        or policy.max_provider_jobs != 3
        or policy.max_pubs != 52
        or policy.max_total_shots != 49664
        or policy.max_concurrent_unresolved_own_jobs != 1
        or policy.max_cost_eur != 0
        or policy.required_plan != "open"
        or policy.required_pricing_type != "free"
        or (
            policy.version == BUDGET_B_VERSION
            and not re.fullmatch(r"[0-9a-f]{64}", policy.budget_authorization_sha256 or "")
        )
    ):
        raise SafetyError("Runtime options require the exact resolved budget policy")
    if type(max_execution_time) is not int or not 1 <= max_execution_time <= maximum:
        raise SafetyError(f"QPU execution cap must be between 1 and {maximum} seconds")
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


def validate_runtime_options(
    options: Mapping[str, Any], *, budget_policy: BudgetPolicy | None = None
) -> None:
    """Reject unknown keys and implicit optional processing."""
    execution = options.get("execution")
    if not isinstance(execution, dict):
        raise SafetyError("Explicit Sampler execution options are required")
    expected = safe_runtime_options(
        options.get("max_execution_time", 0),
        rep_delay=execution.get("rep_delay"),
        budget_policy=budget_policy,
    )
    if dict(options) != expected:
        raise SafetyError("Unknown or non-approved Sampler options")


def sdk_option_contract(
    options: Mapping[str, Any], *, budget_policy: BudgetPolicy | None = None
) -> dict[str, Any]:
    """Validate real pinned SDK option construction entirely offline."""
    validate_runtime_options(options, budget_policy=budget_policy)
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


def plan_budget_policy(plan: Mapping[str, Any]) -> BudgetPolicy:
    """Resolve the full public budget proof without granting hardware approval."""
    try:
        policy = resolve_budget_policy(
            plan.get("budget_policy_version", BUDGET_V1_VERSION), plan.get("budget_authorization")
        )
    except (ValueError, TypeError) as error:
        raise SafetyError(str(error), diagnostic_kind="PROVIDER_CONTRACT_ERROR") from None
    expected: dict[str, Any] = {
        "max_provider_jobs": policy.max_provider_jobs,
        "max_total_shots": policy.max_total_shots,
        "max_qpu_seconds_per_job": policy.max_qpu_seconds_per_job,
        "max_total_qpu_seconds": policy.max_total_qpu_seconds,
        "max_cost_eur": policy.max_cost_eur,
    }
    if policy.version == BUDGET_B_VERSION:
        expected.update(
            per_job_qpu_seconds=policy.per_job_qpu_seconds,
            budget_authorization_sha256=policy.budget_authorization_sha256,
        )
    elif "job_runtime_options" in plan or "budget_authorization" in plan:
        raise SafetyError("Historical V1 cannot carry Budget B execution fields")
    if plan.get("budget") != expected:
        raise SafetyError("Plan budget differs from its exact authorized policy")
    return policy


def runtime_options_for_job(plan: Mapping[str, Any], job_id: str) -> dict[str, Any]:
    """Bind actual per-job Sampler options; only execution time varies in B."""
    import copy

    policy = plan_budget_policy(plan)
    global_options = plan.get("runtime_options", {})
    validate_runtime_options(global_options, budget_policy=policy)
    if global_options["max_execution_time"] != policy.max_qpu_seconds_per_job:
        raise SafetyError("Global runtime options differ from the authorized scalar guard")
    if policy.version == BUDGET_V1_VERSION:
        return copy.deepcopy(dict(global_options))
    rows = plan.get("job_runtime_options")
    if not isinstance(rows, Mapping) or set(rows) != set(JOB_IDS):
        raise SafetyError("Budget B requires all three exact per-job runtime options")
    for identity in JOB_IDS:
        expected = {**global_options, "max_execution_time": policy.cap_for_job(identity)}
        if rows[identity] != expected:
            raise SafetyError("Per-job runtime options differ from the authorized budget")
        validate_runtime_options(rows[identity], budget_policy=policy)
    try:
        return copy.deepcopy(dict(rows[job_id]))
    except KeyError:
        raise SafetyError("Budget B does not authorize this job identity") from None


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
    """Verify live account facts and the exact documentary/local job contracts.

    BackendConfiguration's old max_shots/max_experiments/max_jobs fields are
    observations only. They cannot substitute for modern expanded-execution,
    control-memory, native-circuit and concrete payload assessments.
    """
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
    if not evidence.get("region") or str(evidence.get("status_message", "")).lower() != "active":
        raise SafetyError("Explicit instance region and active accepting backend are required")
    for key in ("remaining_seconds", "consumed_seconds", "limit_seconds"):
        if not _known_nonnegative(usage.get(key)):
            raise SafetyError("Current usage and quota must be known")
    usage_age = (current - parse_time(usage.get("observed_at", ""))).total_seconds()
    if usage_age < 0 or usage_age > MAX_PREFLIGHT_AGE_SECONDS:
        raise SafetyError("Usage observation is stale or future dated")
    if usage.get("usage_limit_reached") is not False:
        raise SafetyError("Current account usage_limit_reached must be explicitly false")
    if (
        usage["limit_seconds"] <= 0
        or usage["remaining_seconds"] > usage["limit_seconds"]
        or usage["consumed_seconds"] + usage["remaining_seconds"] > usage["limit_seconds"] + 1e-6
    ):
        raise SafetyError("Provider usage and remaining allocation are inconsistent")
    pending = evidence.get("pending_jobs", {})
    if (
        pending.get("complete") is not True
        or pending.get("status") != "VERIFIED"
        or type(pending.get("count")) is not int
        or pending["count"] != 0
    ):
        raise SafetyError("Non-final or unknown provider job reservations must be resolved")
    pending_age = (current - parse_time(pending.get("observed_at", ""))).total_seconds()
    if pending_age < 0 or pending_age > MAX_PREFLIGHT_AGE_SECONDS:
        raise SafetyError("Provider job reservation observation is stale or future dated")
    from km_quantum.m5.provider_limits import (
        LimitContractError,
        validate_limit_assessment,
        validate_service_limit_policy,
    )

    policy = evidence.get("service_limits", {})
    try:
        validate_service_limit_policy(policy)
    except (LimitContractError, TypeError) as error:
        raise SafetyError(str(error), diagnostic_kind="PROVIDER_CONTRACT_ERROR") from None
    if not evidence.get("backend") or not evidence.get("instance_binding"):
        raise SafetyError("Explicit provider binding is missing")
    if plan is None:
        return
    if any(evidence.get(key) != plan.get(key) for key in ("backend", "instance_binding")):
        raise SafetyError("Provider instance or backend differs from the execution plan")
    budget_policy = plan_budget_policy(plan)
    jobs = plan["jobs"]
    reserved = sum(job["max_execution_time"] for job in jobs)
    if (
        len(jobs) > budget_policy.max_provider_jobs
        or reserved > budget_policy.max_total_qpu_seconds
        or any(
            type(job["max_execution_time"]) is not int
            or job["max_execution_time"] != budget_policy.cap_for_job(job["id"])
            for job in jobs
        )
        or sum(pub["shots"] for job in jobs for pub in job["pubs"]) > budget_policy.max_total_shots
    ):
        raise SafetyError(
            "Authorized local job/shot/QPU/cost budget changed",
            diagnostic_kind="PROVIDER_CONTRACT_ERROR",
        )
    for job in jobs:
        runtime_options_for_job(plan, job["id"])
    if reserved > usage["remaining_seconds"]:
        raise SafetyError("Remaining provider quota does not cover the full approved reservation")
    try:
        validate_limit_assessment(plan, policy)
    except (LimitContractError, KeyError, TypeError, ValueError) as error:
        raise SafetyError(str(error), diagnostic_kind="PROVIDER_CONTRACT_ERROR") from None
    model = plan.get("local_usage_model")
    if not isinstance(model, Mapping):
        raise SafetyError(
            "Concrete local target usage model is required",
            diagnostic_kind="INSUFFICIENT_USAGE_MODEL",
        )
    from km_quantum.m5.usage import validate_usage_model

    try:
        validate_usage_model(model, plan, require_ready=False)
    except (ValueError, KeyError, TypeError) as error:
        raise SafetyError(str(error), diagnostic_kind="INSUFFICIENT_USAGE_MODEL") from None
    over_cap = any(
        _known_nonnegative(row.get("local_policy_seconds"))
        and row["local_policy_seconds"] > budget_policy.cap_for_job(row["job_id"])
        for row in model.get("jobs", [])
    ) or (
        _known_nonnegative(model.get("local_policy_total_seconds"))
        and model["local_policy_total_seconds"] > budget_policy.max_total_qpu_seconds
    )
    if over_cap:
        raise SafetyError(
            "Local target usage scenario exceeds the authorized job/total caps",
            diagnostic_kind="USAGE_LIMIT_EXCEEDED",
        )
    try:
        validate_usage_model(model, plan)
    except (ValueError, KeyError, TypeError) as error:
        raise SafetyError(str(error), diagnostic_kind="INSUFFICIENT_USAGE_MODEL") from None
    if evidence.get("local_usage_model") != model or evidence.get(
        "execution_estimates"
    ) != model.get("execution_estimates"):
        raise SafetyError(
            "Fresh preflight must retain the full plan-bound local usage model",
            diagnostic_kind="INSUFFICIENT_USAGE_MODEL",
        )
    for job in jobs:
        estimate = model["execution_estimates"][job["id"]]
        if (
            job.get("estimated_qpu_seconds") != estimate["seconds"]
            or job.get("estimate_source") != estimate["source"]
        ):
            raise SafetyError(
                "Plan job estimate mirror differs from its complete local usage model",
                diagnostic_kind="INSUFFICIENT_USAGE_MODEL",
            )


def preflight(
    config: LocalProviderConfig,
    adapter: ProviderAdapter,
    plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Explicit read boundary; no dry-run job, polling or submission."""
    evidence: dict[str, Any] = {}
    try:
        evidence = adapter.metadata(config, plan)
        evidence["instance_binding"] = config.binding
        evidence["backend"] = config.backend
        evidence["source_kind"] = adapter.source_kind
        evidence["single_job_contract"] = adapter.single_job_contract
        secret_values = (config.token or "", config.instance, config.account_name or "")
        private_secrets = getattr(adapter, "_secret_values", None)
        if callable(private_secrets):
            secret_values += tuple(private_secrets())
        if adapter.source_kind != "ibm_hardware":
            evidence.update(status="OFFLINE_READY", provider_verified=False, hardware_used=False)
            return dict(redact(evidence, secrets=secret_values))
        validate_preflight(evidence, plan)
        evidence["status"] = "PROVIDER_PREFLIGHT_READY"
        evidence["hardware_used"] = False
        return dict(redact(evidence, secrets=secret_values))
    except Exception as error:
        kind = getattr(error, "diagnostic_kind", "OWNER_CONFIGURATION_REQUIRED")
        secret_values = (config.token or "", config.instance, config.account_name or "")
        private_secrets = getattr(adapter, "_secret_values", None)
        if callable(private_secrets):
            secret_values += tuple(private_secrets())
        # Retain the successful live reads: a local contract/model bug is not
        # evidence that the owner should re-enter valid credentials.
        evidence.update(
            {
                "status": "OWNER_CONFIGURATION_REQUIRED"
                if kind == "OWNER_CONFIGURATION_REQUIRED"
                else "BLOCKED",
                "provider_verified": evidence.get("provider_verified", False),
                "hardware_used": False,
                "backend": config.backend,
                "instance_binding": config.binding,
                "diagnostic_kind": kind,
                "diagnostic": str(error),
                "diagnostics": [
                    {"kind": kind, "exception_type": type(error).__name__, "message": str(error)}
                ],
            }
        )
        return dict(redact(evidence, secrets=secret_values))


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

    def __init__(
        self, config: LocalProviderConfig, *, budget_policy: BudgetPolicy | None = None
    ) -> None:
        self.config = config
        self.budget_policy = budget_policy or resolve_budget_policy()
        self._service: Any = None
        self._backend: Any = None
        self._known_secrets: tuple[str, ...] = ()

    def _secret_values(self) -> tuple[str, ...]:
        """Private already-loaded named credential; never read account files."""
        token = getattr(getattr(self._service, "_account", None), "token", None)
        return (
            *self._known_secrets,
            token if isinstance(token, str) else "",
            self.config.token or "",
            self.config.instance,
            self.config.account_name or "",
        )

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
                token = getattr(getattr(service, "_account", None), "token", None)
                self._known_secrets = (token,) if isinstance(token, str) else ()
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
            if (
                self.config.account_name
                and not self._known_secrets
                and not isinstance(error, SafetyError)
            ):
                # Constructor failure can precede any readable SDK account.
                # An opaque stored API key cannot be safely identified there.
                raise SafetyError(
                    "Named account connection failed; private SDK detail suppressed"
                ) from None
            raise SafetyError(
                redact(str(error), secrets=self._secret_values()),
                diagnostic_kind=getattr(error, "diagnostic_kind", "OWNER_CONFIGURATION_REQUIRED"),
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
        # pending=True in SDK 0.47 includes QUEUED/RUNNING only and could miss
        # INITIALIZING. Fetch a bounded unfiltered inventory instead, then read
        # each status once. A truncated inventory is honestly incomplete.
        pending: dict[str, Any] = {
            "status": "UNKNOWN",
            "complete": False,
            "count": None,
            "observed_at": now,
            "scope": "explicit active instance",
            "source": "SDK jobs(limit=101,pending=None,instance=explicit); status once",
            "reservation_seconds": None,
        }
        if callable(getattr(self._service, "jobs", None)):
            try:
                with _quiet_sdk():
                    jobs = self._service.jobs(limit=101, pending=None, instance=config.instance)
                    states = [str(job.status()).upper() for job in jobs]
                final = {"DONE", "CANCELLED", "ERROR"}
                known = final | {"INITIALIZING", "QUEUED", "RUNNING"}
                complete = len(jobs) < 101 and all(state in known for state in states)
                nonfinal = sum(state not in final for state in states)
                pending.update(
                    status="VERIFIED" if complete else "UNKNOWN",
                    complete=complete,
                    count=nonfinal if complete else None,
                    inspected_jobs=len(jobs),
                    reservation_seconds=0 if complete and nonfinal == 0 else None,
                )
            except Exception as error:
                pending["diagnostic"] = redact(str(error), secrets=self._secret_values())
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
            "status_message": getattr(status, "status_msg", None),
            "region": config.instance.split(":")[5]
            if len(config.instance.split(":")) > 5
            else None,
            "region_source": "Provider-returned selected instance CRN location",
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
                "usage_limit_reached": usage.get("usage_limit_reached"),
                "period": usage.get("usage_period"),
                "time_available": usage.get("usage_time_available"),
                "remaining_source": "SDK instance difference; separate admission flag required",
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
            "pending_jobs": pending,
        }
        from km_quantum.m5.provider_limits import service_limit_policy

        result["service_limits"] = service_limit_policy()
        if plan is not None:
            import copy

            model = plan.get("local_usage_model")
            if isinstance(model, Mapping):
                result["local_usage_model"] = copy.deepcopy(dict(model))
                result["execution_estimates"] = copy.deepcopy(model.get("execution_estimates", {}))
        # Missing service facts are kept unknown. No inferred paid fallback or
        # fabricated job capacity converts this into READY_FOR_APPROVAL.
        return result

    def submit(self, pubs: Sequence[Any], options: Mapping[str, Any], tags: Sequence[str]) -> Any:
        sdk_option_contract(options, budget_policy=self.budget_policy)
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
