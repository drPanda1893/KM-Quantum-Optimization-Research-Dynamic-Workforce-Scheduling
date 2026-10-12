"""Atomic reservation before submission; immutable PUB identity on collection.

An ambiguous submission keeps its full reservation forever. Resume/collect can
read existing IDs, but never submits a replacement or silently retries a job.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from km_quantum.m5.approvals import plan_hash, validate_approval, validate_plan
from km_quantum.m5.provider import ProviderAdapter, SafetyError, redact, utc_now


def _atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class JobLedger:
    """Single local owner, maximum three reservations and no resubmission."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._secrets: tuple[str, ...] = ()

    def _bind_secrets(self, adapter: ProviderAdapter) -> None:
        config = getattr(adapter, "config", None)
        self._secrets = (
            getattr(config, "token", "") or "",
            getattr(config, "instance", "") or "",
        )

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise SafetyError("Ledger is locked; stale locks require manual owner review") from None
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(str(os.getpid()))
                stream.flush()
                os.fsync(stream.fileno())
            yield
        finally:
            self.lock_path.unlink(missing_ok=True)

    def snapshot(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "artifact_type": "m5_job_ledger",
                "schema_version": 1,
                "plan_hash": None,
                "hardware_enabled": False,
                "submission_approved": False,
                "reserved_qpu_seconds": 0,
                "reserved_shots": 0,
                "jobs": [],
            }
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("artifact_type") != "m5_job_ledger" or data.get("schema_version") != 1:
            raise SafetyError("Ledger artifact schema is invalid")
        return dict(data)

    def _save(self, data: dict[str, Any]) -> None:
        data["updated_at"] = utc_now()
        _atomic_write(self.path, redact(data, secrets=self._secrets))

    @staticmethod
    def _check_binding(data: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
        if data.get("plan_hash") not in {None, plan_hash(plan)}:
            raise SafetyError("Ledger belongs to a different execution plan")
        records = data["jobs"]
        job_ids = [record.get("job_id") for record in records]
        if len(set(job_ids)) != len(job_ids):
            raise SafetyError("Ledger contains duplicate job identities")
        provider_ids = [
            record["provider_job_id"] for record in records if record.get("provider_job_id")
        ]
        if len(provider_ids) != len(set(provider_ids)):
            raise SafetyError("Ledger contains duplicate provider job IDs")
        if job_ids != [job["id"] for job in plan["jobs"][: len(records)]]:
            raise SafetyError("Ledger job order differs from the execution plan")
        reserved_seconds = sum(record["reserved_qpu_seconds"] for record in records)
        reserved_shots = sum(sum(pub["shots"] for pub in record["pubs"]) for record in records)
        if (
            data["reserved_qpu_seconds"] != reserved_seconds
            or data["reserved_shots"] != reserved_shots
        ):
            raise SafetyError("Ledger reservations are inconsistent")
        for record, job in zip(records, plan["jobs"], strict=False):
            if (
                record.get("pubs") != job["pubs"]
                or record.get("plan_hash") != plan_hash(plan)
                or record.get("reserved_qpu_seconds") != job["max_execution_time"]
            ):
                raise SafetyError("Persistent PUB identities or reservations changed")

    @staticmethod
    def _load_pubs(
        job: Mapping[str, Any], plan: Mapping[str, Any], circuits: Mapping[str, str | Path]
    ) -> list[Any]:
        from qiskit import qpy

        pubs: list[Any] = []
        for pub in job["pubs"]:
            path = Path(circuits[pub["circuit_id"]])
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != pub["circuit_hash"]:
                raise SafetyError("Provider QPY bytes differ from the approved circuit hash")
            with path.open("rb") as stream:
                loaded = qpy.load(stream)
            if len(loaded) != 1:
                raise SafetyError("One frozen QPY circuit per PUB is required")
            circuit = loaded[0]
            registers = [register for register in circuit.cregs if register.name == "m2"]
            if (
                len(registers) != 1
                or registers[0].size != pub["num_bits"]
                or circuit.num_clbits != pub["num_bits"]
                or circuit.num_parameters
            ):
                raise SafetyError("Provider circuit requires a single fixed named m2 register")
            measurement_bits = [
                circuit.find_bit(instruction.clbits[0]).index
                for instruction in circuit.data
                if instruction.operation.name == "measure"
            ]
            if sorted(measurement_bits) != list(range(pub["num_bits"])):
                raise SafetyError("Every m2 bit requires exactly one terminal measurement")
            measurement_started = False
            for instruction in circuit.data:
                if instruction.operation.name == "measure":
                    measurement_started = True
                elif measurement_started and instruction.operation.name != "barrier":
                    raise SafetyError("Provider circuits require terminal measurements")
            metadata = dict(circuit.metadata or {})
            metadata.update(m5_pub_id=pub["pub_id"], m5_circuit_hash=actual)
            circuit.metadata = metadata
            pubs.append((circuit, None, pub["shots"]))
        return pubs

    def submit_next(
        self,
        plan: Mapping[str, Any],
        approval: Mapping[str, Any],
        evidence: Mapping[str, Any],
        adapter: ProviderAdapter,
        circuits: Mapping[str, str | Path],
        *,
        now: str | None = None,
    ) -> dict[str, Any]:
        self._bind_secrets(adapter)
        validate_approval(plan, approval, evidence, now=now)
        if adapter.source_kind != "ibm_hardware" or adapter.single_job_contract is not True:
            raise SafetyError("Submission requires the reviewed single-job hardware adapter")
        config = getattr(adapter, "config", None)
        if config is not None and (
            config.binding != plan["instance_binding"] or config.backend != plan["backend"]
        ):
            raise SafetyError("Submission adapter binding differs from the approved plan")
        with self._lock():
            data = self.snapshot()
            self._check_binding(data, plan)
            records = data["jobs"]
            if records and records[-1]["status"] != "COLLECTED":
                raise SafetyError("Prior job is pending, failed or unknown; no retry or next job")
            if len(records) >= len(plan["jobs"]):
                raise SafetyError("All approved jobs have already been reserved")
            if records and records[0].get("contract_verified") is not True:
                raise SafetyError("Smoke result contract must be verified before main jobs")
            next_job = plan["jobs"][len(records)]
            pubs = self._load_pubs(next_job, plan, circuits)
            requested_shots = sum(pub["shots"] for pub in next_job["pubs"])
            cap = next_job["max_execution_time"]
            budget = plan["budget"]
            if (
                len(records) + 1 > budget["max_provider_jobs"]
                or data["reserved_qpu_seconds"] + cap > budget["max_total_qpu_seconds"]
                or data["reserved_shots"] + requested_shots > budget["max_total_shots"]
            ):
                raise SafetyError("Approved reservation budget is exhausted")
            digest = plan_hash(plan)

            def canonical_hash(value: Any) -> str:
                return hashlib.sha256(
                    json.dumps(
                        value,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    ).encode()
                ).hexdigest()

            record: dict[str, Any] = {
                "job_id": next_job["id"],
                "local_job_id": "m5-" + digest[:16] + "-" + next_job["id"],
                "kind": next_job["kind"],
                "plan_hash": digest,
                "provider_job_id": None,
                # Persist ambiguity BEFORE run. Abrupt process death cannot turn
                # an accepted request into a resumable empty reservation.
                "status": "SUBMISSION_UNKNOWN",
                "reserved_qpu_seconds": cap,
                "requested_runtime_options": copy.deepcopy(plan["runtime_options"]),
                "pubs": copy.deepcopy(next_job["pubs"]),
                "created_at": utc_now(),
                "contract_verified": False,
                "hardware_used": False,
                "source_kind": (
                    "ibm_hardware" if getattr(adapter, "hardware_used", False) else "mock"
                ),
                "single_job_contract": adapter.single_job_contract,
                "approval_evidence": {
                    "approved": True,
                    "signed_by": approval["signed_by"],
                    "plan_hash": digest,
                    "signed_at": approval["signed_at"],
                    "expires_at": approval["expires_at"],
                    "validated_at": now or utc_now(),
                    "approval_sha256": canonical_hash(approval),
                    "preflight_sha256": canonical_hash(evidence),
                },
            }
            records.append(record)
            data.update(
                plan_hash=digest,
                hardware_enabled=True,
                submission_approved=True,
                reserved_qpu_seconds=data["reserved_qpu_seconds"] + cap,
                reserved_shots=data["reserved_shots"] + requested_shots,
            )
            self._save(data)
            try:
                provider_job = adapter.submit(
                    pubs, plan["runtime_options"], [record["local_job_id"], "m5-plan-" + digest]
                )
                identity = provider_job.job_id()
                if not isinstance(identity, str) or not identity:
                    raise SafetyError("Provider returned no unambiguous job ID")
                if any(item.get("provider_job_id") == identity for item in records[:-1]):
                    raise SafetyError("Provider returned a duplicate job ID")
                record.update(
                    provider_job_id=identity,
                    status="SUBMITTED",
                    submitted_at=utc_now(),
                )
                self._save(data)  # Immediate ID persistence before all other reads.
                if hasattr(adapter, "verify_job_binding"):
                    adapter.verify_job_binding(provider_job)
            except BaseException as error:
                record.update(
                    status="SUBMISSION_UNKNOWN",
                    diagnostic=redact(str(error), secrets=self._secrets),
                )
                self._save(data)
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
            return copy.deepcopy(record)

    def collect(self, plan: Mapping[str, Any], adapter: ProviderAdapter) -> dict[str, Any]:
        """One status read per known ID; only completed jobs have results fetched."""
        self._bind_secrets(adapter)
        validate_plan(plan)
        with self._lock():
            data = self.snapshot()
            self._check_binding(data, plan)
            raw: list[dict[str, Any]] = []
            for record in data["jobs"]:
                if record.get("raw_results"):
                    raw.extend(copy.deepcopy(record["raw_results"]))
                if record["status"] == "COLLECTED" or not record.get("provider_job_id"):
                    continue
                try:
                    provider_job = adapter.job(record["provider_job_id"])
                    if provider_job.job_id() != record["provider_job_id"]:
                        raise SafetyError("Provider job ID differs from persistent ledger ID")
                    if hasattr(adapter, "verify_job_binding"):
                        adapter.verify_job_binding(provider_job)
                    status = provider_job.status()
                    status_name = getattr(status, "name", str(status)).upper()
                    record["provider_status"] = status_name
                    record["last_collected_at"] = utc_now()
                    if status_name in {"ERROR", "FAILED", "CANCELLED"}:
                        diagnostic = (
                            provider_job.error_message()
                            if hasattr(provider_job, "error_message")
                            else status_name
                        )
                        record.update(
                            status="CANCELLED" if status_name == "CANCELLED" else "FAILED",
                            diagnostic=redact(diagnostic),
                        )
                    elif status_name in {"DONE", "COMPLETED"}:
                        result = provider_job.result(timeout=1)
                        actual_source = (
                            "hardware" if getattr(adapter, "hardware_used", False) else "mock"
                        )
                        parsed, diagnostics = parse_sampler_result(result, record, actual_source)
                        record["raw_results"] = parsed
                        raw = [item for item in raw if item["job_id"] != record["job_id"]]
                        raw.extend(parsed)
                        record["result_diagnostics"] = diagnostics
                        record["provider_metadata"] = redact(getattr(result, "metadata", {}))
                        if hasattr(provider_job, "metrics"):
                            record["provider_metrics"] = redact(provider_job.metrics())
                        record["contract_verified"] = not diagnostics
                        record["status"] = "COLLECTED" if not diagnostics else "PARTIAL"
                        record["hardware_used"] = actual_source == "hardware"
                    else:
                        record["status"] = "SUBMITTED"
                except Exception as error:
                    record.update(
                        status="PARTIAL", diagnostic=redact(str(error), secrets=self._secrets)
                    )
                self._save(data)
            expected_ids = [pub["pub_id"] for job in plan["jobs"] for pub in job["pubs"]]
            actual_ids = {pub["pub_id"] for pub in raw}
            missing = [identity for identity in expected_ids if identity not in actual_ids]
            complete = len(data["jobs"]) == 3 and all(
                record["status"] == "COLLECTED" for record in data["jobs"]
            )
            any_hardware = any(item.get("hardware_used") is True for item in raw)
            status = "COLLECTED" if complete else "PARTIAL" if raw else "PENDING_HARDWARE"
            if complete and not any_hardware:
                status = "OFFLINE_READY"
            if any(record["status"] == "SUBMISSION_UNKNOWN" for record in data["jobs"]):
                status = "SUBMISSION_UNKNOWN"
            return dict(
                redact(
                    {
                        "status": status,
                        "hardware_used": any_hardware,
                        "raw_results": raw,
                        "missing_pubs": missing,
                        "job_records": copy.deepcopy(data["jobs"]),
                        "reserved_qpu_seconds": data["reserved_qpu_seconds"],
                    },
                    secrets=self._secrets,
                )
            )


def parse_sampler_result(
    result: Any, record: Mapping[str, Any], source: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Sampler V2 classified BitArray for the named, unbroadcast m2 register."""
    from qiskit.primitives.containers import BitArray

    if source not in {"hardware", "mock"}:
        raise SafetyError("Result provenance must be hardware or mock")
    parsed: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    try:
        size = len(result)
    except TypeError:
        return [], [{"diagnostic": "Provider result is not an indexed PrimitiveResult"}]
    if size != len(record["pubs"]):
        diagnostics.append(
            {"diagnostic": "Provider PUB count differs from persistent job", "actual_pubs": size}
        )
    for index, pub in enumerate(record["pubs"]):
        if index >= size:
            diagnostics.append({"pub_id": pub["pub_id"], "diagnostic": "Missing PUB result"})
            continue
        observed_counts: Any = None
        try:
            item = result[index]
            data = item.data
            if not hasattr(data, "m2") or set(data.keys()) != {"m2"}:
                raise SafetyError("Result requires exactly one named m2 register")
            bits = data.m2
            if not isinstance(bits, BitArray) or bits.shape != ():
                raise SafetyError("m2 must be an unbroadcast classified BitArray")
            observed_counts = bits.get_counts()
            if bits.num_bits != pub["num_bits"] or bits.num_shots != pub["shots"]:
                raise SafetyError("BitArray width or actual shots differ from the approved PUB")
            counts = observed_counts
            validate_counts(counts, pub["num_bits"], pub["shots"])
            metadata = getattr(item, "metadata", {})
            if metadata.get("m5_pub_id", pub["pub_id"]) != pub["pub_id"]:
                raise SafetyError("Provider PUB identity differs from persistent index mapping")
            if metadata.get("m5_circuit_hash", pub["circuit_hash"]) != pub["circuit_hash"]:
                raise SafetyError("Provider circuit identity differs from persistent index mapping")
            parsed.append(
                {
                    **copy.deepcopy(pub),
                    "circuit_sha256": pub["circuit_hash"],
                    "pub_index": index,
                    "job_id": record["job_id"],
                    "provider_job_id": record["provider_job_id"],
                    "counts": dict(sorted(counts.items())),
                    "source": source,
                    "hardware_used": source == "hardware",
                    "requested_runtime_options": copy.deepcopy(record["requested_runtime_options"]),
                    "provider_metadata": redact(metadata),
                }
            )
        except Exception as error:
            diagnostics.append(
                {
                    "pub_id": pub["pub_id"],
                    "diagnostic": redact(str(error)),
                    "observed_counts": observed_counts,
                }
            )
    return parsed, diagnostics


def validate_counts(counts: Mapping[str, Any], num_bits: int, shots: int) -> None:
    if not counts:
        raise SafetyError("Raw counts are missing")
    if any(
        not isinstance(bits, str)
        or len(bits) != num_bits
        or set(bits) - {"0", "1"}
        or type(count) is not int
        or count < 0
        for bits, count in counts.items()
    ):
        raise SafetyError("Raw m2 counts require fixed-width bits and nonnegative integers")
    if sum(counts.values()) != shots:
        raise SafetyError("Raw count sum differs from the approved shot count")
