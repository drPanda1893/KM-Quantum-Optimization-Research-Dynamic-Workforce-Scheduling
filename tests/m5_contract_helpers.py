"""Explicit synthetic numerical contracts for offline approval/ledger tests."""

from typing import Any

from qiskit import QuantumCircuit, QuantumRegister
from qiskit.circuit import ClassicalRegister, Measure, Reset
from qiskit.circuit.library import XGate
from qiskit.transpiler import InstructionProperties, Target

from km_quantum.m5.provider_limits import assess_plan_limits, service_limit_policy
from km_quantum.m5.usage import UsagePolicy, estimate_plan_usage


def attach_execution_contracts(plan: dict[str, Any]) -> dict[str, Any]:
    """Reviewed one-partition *synthetic* scenario; production defaults stay intact."""
    width = max([16, *[int(value) + 1 for value in plan["layout"]]])
    target = Target(num_qubits=width)
    properties = {(qubit,): InstructionProperties(duration=1e-6, error=0) for qubit in range(width)}
    for operation in (XGate(), Measure(), Reset()):
        target.add_instruction(operation, properties.copy())
    circuits = {}
    for identity, row in plan["circuits"].items():
        pub = next(
            pub for job in plan["jobs"] for pub in job["pubs"] if pub["circuit_id"] == identity
        )
        row["num_bits"] = pub["num_bits"]
        circuit = QuantumCircuit(QuantumRegister(width), ClassicalRegister(pub["num_bits"], "m2"))
        circuit.x(plan["layout"][0])
        for bit, physical in enumerate(plan["layout"][: pub["num_bits"]]):
            circuit.measure(physical, bit)
        circuits[identity] = circuit
    plan.setdefault("target_snapshot_sha256", "e" * 64)
    hashes = {identity: row["qpy_sha256"] for identity, row in plan["circuits"].items()}
    rep_delay = {
        "supported": True,
        "value": plan["runtime_options"]["execution"].get("rep_delay", 0.00025),
        "range": [0, 0.001],
        "source": "synthetic explicit backend default",
    }
    model = estimate_plan_usage(
        plan,
        circuits,
        target,
        rep_delay=rep_delay,
        target_snapshot_sha256=plan["target_snapshot_sha256"],
        qpy_hashes=hashes,
        policy=UsagePolicy(
            subjob_policy="reviewed_partition_scenario",
            max_subjobs=1,
            reviewed_source="synthetic test fixture; no IBM partition claim",
        ),
    )
    plan["local_usage_model"] = model
    plan["usage_model_sha256"] = model["model_sha256"]
    assessment = assess_plan_limits(plan, circuits, qpy_hashes=hashes)
    plan["provider_limit_assessment"] = assessment
    plan["provider_limit_assessment_sha256"] = assessment["content_sha256"]
    for job in plan["jobs"]:
        estimate = model["execution_estimates"][job["id"]]
        job.update(estimated_qpu_seconds=estimate["seconds"], estimate_source=estimate["source"])
    return plan


def modern_evidence(evidence: dict[str, Any], plan: dict[str, Any] | None = None) -> dict[str, Any]:
    evidence.setdefault("status", "PROVIDER_PREFLIGHT_READY")
    evidence.update(
        region="synthetic-region",
        status_message="active",
        service_limits=service_limit_policy(),
        pending_jobs={
            "status": "VERIFIED",
            "complete": True,
            "count": 0,
            "observed_at": evidence["obtained_at"],
        },
    )
    evidence["usage"]["usage_limit_reached"] = False
    if plan is not None:
        evidence["local_usage_model"] = plan["local_usage_model"]
        evidence["execution_estimates"] = plan["local_usage_model"]["execution_estimates"]
    return evidence
