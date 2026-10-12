"""Local Target-based usage model with separate, unchanged execution guards.

No service, primitive or backend execution method is used. The numerical
duration model is an approximation; its local safety policy is not an IBM
quotation, an observed duration or a guaranteed upper bound.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal, TypeGuard

import numpy as np
from qiskit import QuantumCircuit
from qiskit.primitives.containers.sampler_pub import SamplerPub
from qiskit.transpiler import Target

from km_quantum.m5.budget_policy import BUDGET_B_VERSION, BUDGET_V1_VERSION, resolve_budget_policy
from km_quantum.m5.io import canonical_hash
from km_quantum.m5.models import M5Config

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_USAGE_SOURCE = "https://quantum.cloud.ibm.com/docs/en/guides/estimate-job-run-time"
_DURATION_SOURCE = "qiskit 2.5.2 QuantumCircuit.estimate_duration(target, unit='s')"


@dataclass(frozen=True)
class UsagePolicy:
    """Explicit local assumptions; these values are not provider limits."""

    subjob_policy: Literal["one_per_expanded_circuit", "reviewed_partition_scenario", "unknown"] = (
        "one_per_expanded_circuit"
    )
    safety_factor: float = 1.25
    overhead_seconds_per_subjob: float = 2.0
    max_subjobs: int | None = None
    reviewed_source: str | None = None

    def __post_init__(self) -> None:
        if self.subjob_policy not in {
            "one_per_expanded_circuit",
            "reviewed_partition_scenario",
            "unknown",
        }:
            raise ValueError("Unsupported local subjob policy")
        if not _number(self.safety_factor) or self.safety_factor < 1:
            raise ValueError("A finite local safety factor of at least one is required")
        if not _number(self.overhead_seconds_per_subjob) or self.overhead_seconds_per_subjob != 2.0:
            raise ValueError("The documented approximate two-second subjob overhead is fixed")
        if self.subjob_policy == "reviewed_partition_scenario":
            if (
                type(self.max_subjobs) is not int
                or self.max_subjobs <= 0
                or not isinstance(self.reviewed_source, str)
                or not self.reviewed_source.strip()
            ):
                raise ValueError(
                    "Reviewed partition scenario requires positive max_subjobs and source"
                )
        elif self.max_subjobs is not None or self.reviewed_source is not None:
            raise ValueError("Partition review fields require an explicit reviewed scenario")


def _number(value: Any) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _hard_limits(config: M5Config) -> dict[str, Any]:
    policy = config.resolved_budget_policy()
    limits: dict[str, Any] = {
        "max_qpu_seconds_per_job": config.max_qpu_seconds_per_job,
        "max_total_qpu_seconds": config.max_total_qpu_seconds,
        "max_campaign_submissions": config.max_provider_jobs,
        "max_total_shots": config.max_total_shots,
    }
    if policy.version == BUDGET_B_VERSION:
        limits.update(
            budget_policy_version=policy.version,
            per_job_qpu_seconds=policy.per_job_qpu_seconds,
            budget_authorization_sha256=policy.budget_authorization_sha256,
        )
    return limits


def _job_cap(job: Mapping[str, Any], config: M5Config) -> int | None:
    try:
        return config.execution_time_cap(str(job.get("id", "")))
    except ValueError:
        return None


def _matching_plan_budget(plan: Mapping[str, Any], config: M5Config) -> bool:
    try:
        actual = resolve_budget_policy(
            plan.get("budget_policy_version", BUDGET_V1_VERSION), plan.get("budget_authorization")
        )
    except (TypeError, ValueError):
        return False
    return actual.as_dict() == config.resolved_budget_policy().as_dict()


def _job_spec(job: Mapping[str, Any], config: M5Config) -> dict[str, Any]:
    """Exclude estimated output fields, keeping the frozen input specification."""
    return {
        "id": job.get("id"),
        "kind": job.get("kind"),
        "max_execution_time": job.get("max_execution_time", _job_cap(job, config)),
        "pubs": copy.deepcopy(job.get("pubs", [])),
    }


def _rep_delay(evidence: Any, missing: list[str]) -> dict[str, Any]:
    if not isinstance(evidence, Mapping):
        missing.append("rep_delay: explicit backend default, range and source required")
        return {"seconds": None, "source": None, "range_seconds": None}
    value, limits = evidence.get("value"), evidence.get("range")
    valid = (
        evidence.get("supported") is True
        and _number(value)
        and isinstance(evidence.get("source"), str)
        and bool(evidence["source"])
        and isinstance(limits, (tuple, list))
        and len(limits) == 2
        and all(_number(item) for item in limits)
        and limits[0] <= value <= limits[1]
    )
    if not valid:
        missing.append(
            "rep_delay: explicit supported backend default, valid range and source required"
        )
    return {
        "seconds": float(value) if _number(value) else None,
        "source": evidence.get("source") if isinstance(evidence.get("source"), str) else None,
        "range_seconds": list(limits)
        if isinstance(limits, (tuple, list))
        and len(limits) == 2
        and all(_number(x) for x in limits)
        else None,
        "counting": (
            "Added exactly once for each expanded circuit shot; never reduced by this model"
        ),
    }


def _instruction_duration(
    target: Target, name: str, qubits: tuple[int, ...], missing: list[str]
) -> float | None:
    properties = target[name].get(qubits) if name in target else None
    if properties is None and name in target:
        properties = target[name].get(None)
    value = getattr(properties, "duration", None)
    if not _number(value):
        missing.append(f"target duration_seconds:{name}{qubits}")
        return None
    return float(value)


def _structure(circuit: QuantumCircuit) -> dict[str, Any]:
    return {
        "num_qubits": circuit.num_qubits,
        "num_clbits": circuit.num_clbits,
        "global_phase": str(circuit.global_phase),
        "operations": [
            {
                "name": item.operation.name,
                "qubits": [circuit.find_bit(bit).index for bit in item.qubits],
                "clbits": [circuit.find_bit(bit).index for bit in item.clbits],
                "parameters": [str(value) for value in item.operation.params],
                "unit": getattr(item.operation, "unit", None),
            }
            for item in circuit.data
        ],
    }


def _timing(
    circuit: QuantumCircuit, target: Target, missing: list[str]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Analyze a separate copy; hardware/noise circuits receive no scheduling delays."""
    analysis = circuit.copy()
    active: set[int] = set()
    durations: list[dict[str, Any]] = []
    explicit_resets, measurements = 0, 0
    for item in analysis.data:
        name = item.operation.name
        qubits = tuple(analysis.find_bit(bit).index for bit in item.qubits)
        if name == "barrier":
            continue
        active.update(qubits)
        if name == "reset":
            explicit_resets += 1
        if name == "measure":
            measurements += 1
        if name == "delay" or item.operation.name in {
            "if_else",
            "while_loop",
            "for_loop",
            "switch_case",
        }:
            missing.append(f"Unsupported frozen timing instruction:{name}")
            continue
        value = _instruction_duration(target, name, qubits, missing)
        durations.append({"name": name, "qubits": list(qubits), "duration_seconds": value})
    initialization = []
    for qubit in sorted(active):
        reset = _instruction_duration(target, "reset", (qubit,), missing)
        initialization.append(reset)
        durations.append({"name": "reset", "qubits": [qubit], "duration_seconds": reset})
    before = len(missing)
    duration: float | None = None
    if not any(item["duration_seconds"] is None for item in durations) and not missing:
        try:
            value = analysis.estimate_duration(target, unit="s")
        except Exception as error:
            # A local analysis failure is not a request to configure credentials.
            missing.append(f"Circuit duration analysis failed:{type(error).__name__}")
        else:
            if _number(value):
                duration = float(value)
            else:
                missing.append("Circuit duration analysis returned invalid seconds")
    if not measurements:
        missing.append("Circuit readout missing")
    if not active:
        missing.append("Circuit active physical qubits missing")
    initialization_seconds = (
        max(float(item) for item in initialization if item is not None)
        if initialization and all(item is not None for item in initialization)
        else None
    )
    return {
        "circuit_seconds": duration if len(missing) == before else None,
        "initialization_seconds": initialization_seconds,
        "initialization_definition": (
            "One parallel provider initialization reset over active physical qubits per shot"
        ),
        "explicit_reset_count": explicit_resets,
        "explicit_resets_included_in_circuit_seconds": True,
        "measurement_count": measurements,
        "readout_included_once": True,
        "active_physical_qubits": sorted(active),
        "analysis_structure_sha256": canonical_hash(_structure(analysis)),
        "original_structure_sha256": canonical_hash(_structure(circuit)),
    }, durations


def estimate_job_usage(
    job: Mapping[str, Any],
    circuits: Mapping[str, QuantumCircuit],
    target: Target,
    *,
    rep_delay: Any,
    target_snapshot_sha256: str,
    qpy_hashes: Mapping[str, str],
    config: M5Config | None = None,
    policy: UsagePolicy | None = None,
) -> dict[str, Any]:
    """Sum heterogeneous expanded executions against the actual native Target.

    The central model uses one internal subjob. The local policy models one
    internal subjob per expanded circuit and applies a separately disclosed
    safety factor. Neither partition assumption is provider-confirmed.
    """
    config, policy = config or M5Config(), policy or UsagePolicy()
    missing: list[str] = []
    repetition = _rep_delay(rep_delay, missing)
    if not _SHA256.fullmatch(target_snapshot_sha256):
        missing.append("target_snapshot_sha256 binding")
    cap = _job_cap(job, config)
    if cap is None or job.get("max_execution_time", cap) != cap:
        missing.append("max_execution_time must retain the authorized job-specific guard")
    if policy.subjob_policy == "unknown":
        missing.append("subjob_partition_policy")
    pubs = job.get("pubs")
    if not isinstance(pubs, (tuple, list)) or not pubs:
        missing.append("Bound PUB inventory missing")
        pubs = []
    inventory: list[dict[str, Any]] = []
    target_durations: list[dict[str, Any]] = []
    used_hashes: dict[str, str] = {}
    expanded_count, executions, shot_time = 0, 0, 0.0
    for pub in pubs:
        if not isinstance(pub, Mapping):
            missing.append("Invalid bound PUB record")
            continue
        identity = pub.get("circuit_id")
        if not isinstance(identity, str) or not identity:
            missing.append("Bound PUB circuit identity missing")
            continue
        original = circuits.get(identity)
        expected_hash = qpy_hashes.get(identity, "")
        if original is None:
            missing.append(f"Frozen circuit missing:{identity}")
            continue
        if not _SHA256.fullmatch(expected_hash) or pub.get("circuit_hash") != expected_hash:
            missing.append(f"circuit_hash binding:{identity}")
        used_hashes[identity] = expected_hash
        try:
            expanded = SamplerPub.coerce((original, pub.get("parameter_values"), pub.get("shots")))
        except (TypeError, ValueError) as error:
            missing.append(f"Bound PUB expansion failed:{identity}:{type(error).__name__}")
            continue
        count = math.prod(expanded.shape)
        shots = expanded.shots
        if shots is None or count <= 0 or count > 1024:
            missing.append(f"PUB expansion or explicit shots invalid:{identity}")
            continue
        expanded_count += count
        executions += count * shots
        timings: list[dict[str, Any]] = []
        for index in np.ndindex(expanded.shape):
            bound = expanded.parameter_values.bind(original, index)
            timing, durations = _timing(bound, target, missing)
            timing["broadcast_index"] = list(index)
            timing["shots"] = shots
            timings.append(timing)
            target_durations.extend(durations)
            duration, initialization = (
                timing["circuit_seconds"],
                timing["initialization_seconds"],
            )
            if (
                duration is not None
                and initialization is not None
                and repetition["seconds"] is not None
            ):
                shot_time += shots * (duration + initialization + repetition["seconds"])
        inventory.append(
            {
                "pub_id": pub.get("pub_id"),
                "circuit_id": identity,
                "qpy_sha256": expected_hash,
                "broadcast_shape": list(expanded.shape),
                "expanded_circuit_count": count,
                "shots_per_expanded_circuit": shots,
                "executions": count * shots,
                "timings": timings,
            }
        )
    central = shot_time + policy.overhead_seconds_per_subjob if not missing else None
    local_subjobs = (
        policy.max_subjobs
        if policy.subjob_policy == "reviewed_partition_scenario"
        else expanded_count
        if policy.subjob_policy == "one_per_expanded_circuit"
        else None
    )
    local = (
        policy.safety_factor * (shot_time + policy.overhead_seconds_per_subjob * local_subjobs)
        if central is not None and local_subjobs is not None
        else None
    )
    over_limit = local is not None and cap is not None and local > cap
    status = "BLOCKED" if missing or over_limit else "READY"
    target_entries = {canonical_hash(item): item for item in target_durations}
    payload: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "m5_local_job_usage_model",
        "job_id": job.get("id"),
        "status": status,
        "diagnostic_kind": "INSUFFICIENT_USAGE_MODEL"
        if missing
        else "USAGE_LIMIT_EXCEEDED"
        if over_limit
        else None,
        "source": "m5_local_target_usage_model",
        "is_provider_quote": False,
        "is_observed_usage": False,
        "is_guaranteed_upper_bound": False,
        "units": "seconds",
        "method": (
            "Native Target critical path + parallel initialization + explicit rep_delay, "
            "per expanded circuit shot; separate modeled subjob overhead"
        ),
        "sources": [
            {
                "url": _USAGE_SOURCE,
                "documentation_review_date": "2026-10-12",
                "role": "Approximate workload formula and nominal subjob overhead",
            },
            {
                "api": _DURATION_SOURCE,
                "role": "Local critical path in SI seconds on a separate analysis copy",
            },
        ],
        "pub_count": len(pubs),
        "expanded_circuit_count": expanded_count,
        "total_executions": executions,
        "shot_time_seconds": shot_time if not missing else None,
        "central_seconds": central,
        "local_policy_seconds": local,
        "central_subjobs": 1,
        "local_policy_subjobs": local_subjobs,
        "overhead": {
            "seconds_per_subjob": policy.overhead_seconds_per_subjob,
            "definition": (
                "Approximate IBM documentation baseline, not a guaranteed or measured value"
            ),
            "provider_confirmed_partition": False,
        },
        "local_policy": {
            **asdict(policy),
            "is_provider_fact": False,
            "is_upper_bound_guarantee": False,
        },
        "initialization_policy": {
            "enabled": True,
            "counting": (
                "One parallel reset over active qubits per shot; "
                "explicit circuit resets remain separate circuit work"
            ),
        },
        "rep_delay": repetition,
        "hard_limits": _hard_limits(config),
        "circuit_hashes": used_hashes,
        "target_snapshot_sha256": target_snapshot_sha256,
        "target_durations_sha256": canonical_hash(
            {
                "unit": "s",
                "dt_seconds": target.dt,
                "durations": [target_entries[key] for key in sorted(target_entries)],
            }
        ),
        "config_sha256": canonical_hash(
            {
                "study": config.model_dump(mode="json"),
                "policy": asdict(policy),
                "rep_delay": repetition,
                "job": _job_spec(job, config),
            }
        ),
        "study": config.model_dump(mode="json"),
        "job_spec": _job_spec(job, config),
        "pubs": inventory,
        "missing": sorted(set(missing)),
        "uncertainties": [
            "The server's actual internal partition and overhead are not exposed before submission",
            (
                "One subjob per expanded circuit is a disclosed local scenario, "
                "not an IBM guarantee; additional shot or payload splitting is possible"
            ),
            (
                "Parallel reset and Target critical-path timing approximate initialization "
                "and scheduling; runtime alignment and control electronics can add time"
            ),
            (
                "The local safety factor is a policy margin; maximum_execution_time "
                "is a separate enforced guard and not a predicted cost"
            ),
        ],
    }
    payload["model_sha256"] = canonical_hash(payload)
    return payload


def estimate_plan_usage(
    plan: Mapping[str, Any],
    circuits: Mapping[str, QuantumCircuit],
    target: Target,
    *,
    rep_delay: Any,
    target_snapshot_sha256: str,
    qpy_hashes: Mapping[str, str],
    config: M5Config | None = None,
    policy: UsagePolicy | None = None,
) -> dict[str, Any]:
    """Model the frozen job inventory; produce the existing evidence-map shape."""
    config, policy = config or M5Config(), policy or UsagePolicy()
    jobs = [
        estimate_job_usage(
            job,
            circuits,
            target,
            rep_delay=rep_delay,
            target_snapshot_sha256=target_snapshot_sha256,
            qpy_hashes=qpy_hashes,
            config=config,
            policy=policy,
        )
        for job in plan.get("jobs", [])
    ]
    missing: list[str] = []
    if not _matching_plan_budget(plan, config):
        missing.append("Plan budget version or authorization differs from the model configuration")
    if not jobs or len(jobs) > config.max_provider_jobs:
        missing.append("Campaign must contain one to three fixed submissions")
    if plan.get("runtime_options", {}).get("execution", {}).get("init_qubits") is not True:
        missing.append("Explicit enabled provider initialization required")
    if sum(job["total_executions"] for job in jobs) > config.max_total_shots:
        missing.append("Expanded campaign executions exceed unchanged 49664-shot cap")
    central = (
        sum(job["central_seconds"] for job in jobs)
        if all(job["central_seconds"] is not None for job in jobs)
        else None
    )
    local = (
        sum(job["local_policy_seconds"] for job in jobs)
        if all(job["local_policy_seconds"] is not None for job in jobs)
        else None
    )
    if local is not None and local > config.max_total_qpu_seconds:
        missing.append(
            "Local policy campaign estimate exceeds authorized "
            f"{config.max_total_qpu_seconds}-second cap"
        )
    estimates = {
        job["job_id"]: {
            "seconds": job["local_policy_seconds"],
            "source": "m5_local_target_usage_model",
            "model_status": job["status"],
            "is_provider_quote": False,
            "central_seconds": job["central_seconds"],
            "local_policy_seconds": job["local_policy_seconds"],
            "model_sha256": job["model_sha256"],
            "circuit_hashes": job["circuit_hashes"],
            "target_snapshot_sha256": target_snapshot_sha256,
            "missing": job["missing"],
        }
        for job in jobs
    }
    payload = {
        "schema_version": 1,
        "artifact_type": "m5_local_plan_usage_model",
        "status": "READY"
        if not missing and all(job["status"] == "READY" for job in jobs)
        else "BLOCKED",
        "is_provider_quote": False,
        "is_guaranteed_upper_bound": False,
        "central_total_seconds": central,
        "local_policy_total_seconds": local,
        "hard_limits": _hard_limits(config),
        "target_snapshot_sha256": target_snapshot_sha256,
        "jobs": jobs,
        "execution_estimates": estimates,
        "missing": missing,
    }
    payload["model_sha256"] = canonical_hash(payload)
    return payload


def validate_usage_model(
    model: Mapping[str, Any], plan: Mapping[str, Any], *, require_ready: bool = True
) -> None:
    """Check the complete local model against frozen plan inputs, not a lone number.

    Validation performs no timing, network or provider calls. QPY raw-byte
    verification remains the caller's responsibility when loading circuits.
    A BLOCKED report can be checked with ``require_ready=False`` for archiving;
    it still cannot become ready provider evidence.
    """
    if model.get("artifact_type") != "m5_local_plan_usage_model":
        raise ValueError("Complete local plan usage model required")
    if model.get("model_sha256") != canonical_hash(
        {key: value for key, value in model.items() if key != "model_sha256"}
    ):
        raise ValueError("Local plan usage model seal mismatch")
    if (
        model.get("is_provider_quote") is not False
        or model.get("is_guaranteed_upper_bound") is not False
    ):
        raise ValueError("Local usage model provenance must not claim provider guarantees")
    target_hash = plan.get("target_snapshot_sha256")
    if (
        not isinstance(target_hash, str)
        or not _SHA256.fullmatch(target_hash)
        or model.get("target_snapshot_sha256") != target_hash
    ):
        raise ValueError("Local usage model Target snapshot differs from the plan")
    plan_jobs, model_jobs = plan.get("jobs", []), model.get("jobs", [])
    if not plan_jobs or len(plan_jobs) != len(model_jobs):
        raise ValueError("Local usage model job inventory differs from the plan")
    if len({job.get("id") for job in plan_jobs}) != len(plan_jobs):
        raise ValueError("Local usage model requires distinct plan job identities")
    expected_estimates: dict[str, dict[str, Any]] = {}
    for expected, measured in zip(plan_jobs, model_jobs, strict=True):
        if measured.get("model_sha256") != canonical_hash(
            {key: value for key, value in measured.items() if key != "model_sha256"}
        ):
            raise ValueError("Local job usage model seal mismatch")
        study = M5Config.model_validate(measured.get("study"))
        if not _matching_plan_budget(plan, study):
            raise ValueError("Local usage budget version or authorization differs from the plan")
        checked_policy = UsagePolicy(
            **{key: measured["local_policy"][key] for key in asdict(UsagePolicy())}
        )
        if (
            measured.get("job_id") != expected.get("id")
            or measured.get("job_spec") != _job_spec(expected, study)
            or measured.get("hard_limits") != _hard_limits(study)
            or model.get("hard_limits") != _hard_limits(study)
            or measured.get("target_snapshot_sha256") != target_hash
        ):
            raise ValueError("Local usage job, Target or hard limits differ from the plan")
        if measured.get("config_sha256") != canonical_hash(
            {
                "study": study.model_dump(mode="json"),
                "policy": {key: measured["local_policy"][key] for key in asdict(UsagePolicy())},
                "rep_delay": measured["rep_delay"],
                "job": _job_spec(expected, study),
            }
        ):
            raise ValueError("Local usage configuration binding differs from the plan")
        expected_hashes = {
            pub["circuit_id"]: plan.get("circuits", {}).get(pub["circuit_id"], {}).get("qpy_sha256")
            for pub in expected["pubs"]
        }
        if measured.get("circuit_hashes") != expected_hashes or any(
            pub.get("circuit_hash") != expected_hashes[pub["circuit_id"]]
            for pub in expected["pubs"]
        ):
            raise ValueError("Local usage QPY circuit bindings differ from the plan")
        actual_pubs = measured.get("pubs", [])
        if len(actual_pubs) != len(expected["pubs"]):
            raise ValueError("Local usage PUB inventory differs from the plan")
        shot_total, expanded_total, shot_time = 0, 0, 0.0
        for pub, timing_pub in zip(expected["pubs"], actual_pubs, strict=True):
            if (
                pub.get("pub_id") != timing_pub.get("pub_id")
                or pub.get("circuit_id") != timing_pub.get("circuit_id")
                or pub.get("circuit_hash") != timing_pub.get("qpy_sha256")
                or pub.get("shots") != timing_pub.get("shots_per_expanded_circuit")
            ):
                raise ValueError("Local usage PUB identity, QPY or shots differ from the plan")
            count = math.prod(timing_pub.get("broadcast_shape", []))
            if (
                timing_pub.get("expanded_circuit_count") != count
                or timing_pub.get("executions") != count * pub["shots"]
                or len(timing_pub.get("timings", [])) != count
            ):
                raise ValueError("Local usage broadcast execution count is inconsistent")
            expanded_total += count
            shot_total += count * pub["shots"]
            for timing in timing_pub["timings"]:
                if not all(
                    _number(timing.get(key))
                    for key in ("circuit_seconds", "initialization_seconds")
                ):
                    if require_ready:
                        raise ValueError("Local usage duration basis is incomplete")
                    continue
                if timing.get("readout_included_once") is not True:
                    raise ValueError("Local usage readout counting contract differs")
                shot_time += pub["shots"] * (
                    timing["circuit_seconds"]
                    + timing["initialization_seconds"]
                    + measured["rep_delay"]["seconds"]
                )
        if (
            measured.get("total_executions") != shot_total
            or measured.get("expanded_circuit_count") != expanded_total
        ):
            raise ValueError("Local usage total execution inventory is inconsistent")
        execution = plan.get("runtime_options", {}).get("execution", {})
        if (
            execution.get("init_qubits") is not True
            or measured.get("initialization_policy", {}).get("enabled") is not True
        ):
            raise ValueError("Local usage requires enabled initialization")
        if (
            execution.get("rep_delay") is not None
            and execution["rep_delay"] != measured["rep_delay"]["seconds"]
        ):
            raise ValueError("Local usage rep_delay differs from the frozen runtime options")
        if execution.get("rep_delay") is None and not measured["rep_delay"].get("source"):
            raise ValueError("Local usage backend-default rep_delay source missing")
        central, local = measured.get("central_seconds"), measured.get("local_policy_seconds")
        if require_ready and (
            measured.get("status") != "READY"
            or measured.get("missing")
            or not _number(central)
            or not _number(local)
            or local < central
            or local > study.execution_time_cap(expected["id"])
        ):
            raise ValueError(
                "Local usage job model is not ready within the authorized "
                f"{study.execution_time_cap(expected['id'])}-second cap"
            )
        if central is not None:
            local_policy = measured["local_policy"]
            policy_subjobs = (
                checked_policy.max_subjobs
                if checked_policy.subjob_policy == "reviewed_partition_scenario"
                else expanded_total
            )
            if policy_subjobs is None:
                raise ValueError("Local usage partition model is incomplete")
            if (
                not math.isclose(measured["shot_time_seconds"], shot_time, abs_tol=1e-12)
                or not math.isclose(central, shot_time + 2.0, abs_tol=1e-12)
                or measured.get("central_subjobs") != 1
                or measured.get("local_policy_subjobs") != policy_subjobs
                or not math.isclose(
                    local,
                    local_policy["safety_factor"] * (shot_time + 2.0 * policy_subjobs),
                    abs_tol=1e-12,
                )
            ):
                raise ValueError("Local usage arithmetic or subjob policy is inconsistent")
        expected_estimates[expected["id"]] = {
            "seconds": local,
            "source": "m5_local_target_usage_model",
            "model_status": measured["status"],
            "is_provider_quote": False,
            "central_seconds": central,
            "local_policy_seconds": local,
            "model_sha256": measured["model_sha256"],
            "circuit_hashes": measured["circuit_hashes"],
            "target_snapshot_sha256": target_hash,
            "missing": measured["missing"],
        }
    if model.get("execution_estimates") != expected_estimates:
        raise ValueError("Local usage evidence map differs from the complete job models")
    central_values = [job["central_seconds"] for job in model_jobs]
    local_values = [job["local_policy_seconds"] for job in model_jobs]
    expected_central = sum(central_values) if all(_number(x) for x in central_values) else None
    expected_local = sum(local_values) if all(_number(x) for x in local_values) else None
    if (
        model.get("central_total_seconds") != expected_central
        or model.get("local_policy_total_seconds") != expected_local
    ):
        raise ValueError("Local usage campaign totals are inconsistent")
    if require_ready and (
        model.get("status") != "READY"
        or model.get("missing")
        or not _number(model.get("local_policy_total_seconds"))
        or model["local_policy_total_seconds"] > study.max_total_qpu_seconds
        or model["local_policy_total_seconds"]
        != sum(job["local_policy_seconds"] for job in model_jobs)
        or len(model_jobs) > study.max_provider_jobs
        or sum(job["total_executions"] for job in model_jobs) > study.max_total_shots
    ):
        raise ValueError(
            "Local usage campaign model is not ready within the authorized "
            f"{study.max_total_qpu_seconds}-second cap"
        )
