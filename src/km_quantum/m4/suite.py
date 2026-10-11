"""Prespecified M4 diagnostic and new synthetic cases through unchanged M2/M1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from km_quantum.domain import ObjectiveWeights, SchedulingInstance, SolverConfig
from km_quantum.experiments.io import canonical_hash
from km_quantum.instances.generator import GENERATOR_VERSION, GeneratorConfig, generate_instance
from km_quantum.qaoa.references import FrozenReference, load_references, validate_reference
from km_quantum.qubo.encoder import encode_qubo, instance_fingerprint
from km_quantum.qubo.ising import IsingModel
from km_quantum.qubo.models import QUBOConfig, QUBOModel
from km_quantum.qubo.verification import verify_equivalence
from km_quantum.solvers import solve_cp_sat

KNOWN_DIAGNOSTICS = {
    "A_single_job": "simple mandatory assignment",
    "E_overlap": "two conflicting optional jobs",
    "H_workload": "capacity constraint with a workload slack bit",
    "I_multiple_optima": "two symmetric optimal primary schedules",
    "K_bounded_slack": "exploratory known weakness, bounded slack degeneracy",
    "M_active_imbalance": "active range auxiliary objective within ten qubits",
}
NEW_SEEDS = (20261101, 20261102, 20261103, 20261104, 20261105, 20261106)


def fixed_generator_specs() -> tuple[dict[str, Any], ...]:
    """Fixed before QAOA outcomes; the trivial sparse-skills case is retained."""
    base: dict[str, Any] = {
        "number_of_workers": 1,
        "number_of_jobs": 2,
        "number_of_skills": 1,
        "compatibility_density": 1.0,
        "overlap_density": 0.0,
        "workload_tightness": 0.0,
        "priority_distribution": [0.6, 0.3, 0.1],
        "min_duration_minutes": 1,
        "max_duration_minutes": 1,
        "horizon_minutes": 4,
        "must_assign_fraction": 0.0,
        "min_cost_per_hour": 1,
        "max_cost_per_hour": 120,
    }
    variants: tuple[tuple[str, dict[str, Any], int, int], ...] = (
        ("simple", {}, 0, 0),
        ("cost", {"number_of_workers": 2}, 1, 0),
        ("conflict", {"overlap_density": 1.0}, 1, 0),
        ("capacity", {"workload_tightness": 2.0}, 1, 0),
        (
            "sparse_skills",
            {"number_of_workers": 2, "number_of_skills": 2, "compatibility_density": 0.5},
            1,
            0,
        ),
        (
            "imbalance",
            {"number_of_workers": 2, "number_of_jobs": 1, "workload_tightness": 0.5},
            1,
            2,
        ),
    )
    return tuple(
        {
            "reference_id": f"new_{seed}_{label}",
            "selection_reason": f"prespecified generator structure: {label}",
            "generator_version": GENERATOR_VERSION,
            "generator_config": GeneratorConfig(**{**base, **changes, "seed": seed}).model_dump(
                mode="json"
            ),
            "qubo_config": QUBOConfig(
                objective=ObjectiveWeights(
                    unassigned_penalty=7, cost_weight=cost, imbalance_weight=imbalance
                )
            ).model_dump(mode="json"),
        }
        for seed, (label, changes, cost, imbalance) in zip(NEW_SEEDS, variants, strict=True)
    )


def _new_reference(payload: dict[str, Any]) -> FrozenReference:
    reference = FrozenReference(
        name=payload["name"],
        instance=SchedulingInstance.model_validate(payload["instance"]),
        qubo=QUBOModel.model_validate(payload["qubo_model"]),
        ising=IsingModel.model_validate(payload["ising_model"]),
        ground_bits=tuple(payload["ground_bits"]),
        ground_energy=payload["ground_energy"],
        constraint_energy_margin=payload["constraint_energy_margin"],
        reference_hash=canonical_hash(payload),
        qubo_hash=payload["qubo_hash"],
        ising_hash=payload["ising_hash"],
        source_record=payload,
    )
    if payload["instance_fingerprint"] != instance_fingerprint(reference.instance):
        raise ValueError("new frozen input fingerprint mismatch")
    if reference.qubo.config != QUBOConfig.model_validate(payload["qubo_config"]):
        raise ValueError("new frozen configuration mismatch")
    validate_reference(reference)
    return reference


def create_suite(
    root: Path, max_qubits: int = 10
) -> tuple[dict[str, FrozenReference], dict[str, Any]]:
    """Generate only fixed candidates, recording every rejection and oracle outcome."""
    if type(max_qubits) is not int or not 1 <= max_qubits <= 10:
        raise ValueError("M4 max_qubits must be an integer between one and ten")
    old = load_references(root)
    refs: dict[str, FrozenReference] = {}
    selections: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for name, reason in KNOWN_DIAGNOSTICS.items():
        ref = old[name]
        if ref.qubo.number_variables > max_qubits:
            selections.append(
                {
                    "reference_id": name,
                    "dataset": "known_diagnostic",
                    "split": "development",
                    "status": "REJECTED_STRUCTURE",
                    "reason": "qubit_limit",
                    "qubit_count": ref.qubo.number_variables,
                }
            )
            continue
        # Practical CP-SAT comparison is timed afresh; the frozen ground set is
        # the independent scoring oracle and does not enter warm preprocessing.
        classical = solve_cp_sat(
            ref.instance,
            SolverConfig(
                objective=ref.qubo.config.objective,
                time_limit_seconds=60,
                num_search_workers=1,
                random_seed=0,
            ),
        )
        if classical.status != "OPTIMAL" or classical.objective_value != ref.ground_energy:
            failures.append({"reference_id": name, "reason": "CP-SAT reference mismatch"})
        refs[name] = ref
        selections.append(
            {
                "reference_id": name,
                "dataset": "known_diagnostic",
                "split": "development",
                "status": "SELECTED",
                "selection_reason": reason,
                "already_observed_in_m3": True,
                "exploratory_known_weakness": name == "K_bounded_slack",
                "qubit_count": ref.qubo.number_variables,
                "reference_hash": ref.reference_hash,
                "qubo_hash": ref.qubo_hash,
                "ising_hash": ref.ising_hash,
                "cp_sat_reference": classical.model_dump(mode="json"),
            }
        )
    for spec in fixed_generator_specs():
        instance = generate_instance(GeneratorConfig.model_validate(spec["generator_config"]))
        config = QUBOConfig.model_validate(spec["qubo_config"])
        model = encode_qubo(instance, config)
        entry = {
            **spec,
            "dataset": "new_evaluation",
            "split": "evaluation",
            "already_observed_in_m3": False,
            "instance_id": instance.instance_id,
            "instance_fingerprint": instance_fingerprint(instance),
            "qubit_count": model.number_variables,
        }
        if model.number_variables > max_qubits:
            selections.append({**entry, "status": "REJECTED_STRUCTURE", "reason": "qubit_limit"})
            continue
        verification = verify_equivalence(
            instance, config, model=model, max_states=1 << max_qubits, analyze_spectrum=True
        )
        entry["equivalence_verification"] = verification.model_dump(mode="json")
        if verification.status != "PASS":
            finding = {
                "reference_id": spec["reference_id"],
                "reason": "equivalence_failure_is_not_a_selection_criterion",
                "status": verification.status,
            }
            failures.append(finding)
            selections.append({**entry, "status": "EQUIVALENCE_FAILURE"})
            continue
        ground = verification.qubo_result
        payload = {
            "schema_version": 1,
            "name": spec["reference_id"],
            "instance": instance.model_dump(mode="json"),
            "instance_fingerprint": instance_fingerprint(instance),
            "qubo_config": config.model_dump(mode="json"),
            "qubo_model": model.model_dump(mode="json"),
            "ising_model": verification.ising_result.ising_model.model_dump(mode="json"),
            "qubo_hash": canonical_hash(model),
            "ising_hash": canonical_hash(verification.ising_result.ising_model),
            "ground_bits": sorted(state.bits for state in ground.ground_states),
            "ground_energy": ground.ground_energy,
            "constraint_energy_margin": ground.invalid_energy_margin,
        }
        ref = _new_reference(payload)
        refs[ref.name] = ref
        selections.append(
            {
                **entry,
                "status": "SELECTED",
                "reference_hash": ref.reference_hash,
                "qubo_hash": ref.qubo_hash,
                "ising_hash": ref.ising_hash,
                "cp_sat_reference": verification.cp_sat_result.model_dump(mode="json")
                if verification.cp_sat_result is not None
                else None,
            }
        )
    audit = {
        "schema_version": 1,
        "artifact_type": "m4_prespecified_suite_selection",
        "status": "PASS" if not failures else "BLOCKED_EQUIVALENCE",
        "max_qubits": max_qubits,
        "structural_selection_criteria": ["schema valid", "active QUBO bits <= cap"],
        "generator_seeds_fixed_before_qaoa": list(NEW_SEEDS),
        "candidate_count": len(selections),
        "selected_count": len(refs),
        "structural_rejection_count": sum(
            row["status"] == "REJECTED_STRUCTURE" for row in selections
        ),
        "failed_equivalence_findings": failures,
        "selection_uses_qaoa_or_warm_start_results": False,
        "resampling_or_replacement_performed": False,
        "selections": selections,
    }
    return refs, audit


def write_suite(root: Path, references: dict[str, FrozenReference], audit: dict[str, Any]) -> None:
    directory = root / "data" / "synthetic" / "m4"
    directory.mkdir(parents=True, exist_ok=True)
    payloads = [
        {
            "reference_hash": reference.reference_hash,
            "source_record": reference.source_record,
        }
        for name, reference in sorted(references.items())
        if name not in KNOWN_DIAGNOSTICS
    ]
    files = {
        "generator_configs.json": json.dumps(
            list(fixed_generator_specs()), indent=2, sort_keys=True, allow_nan=False
        )
        + "\n",
        "selection_audit.json": json.dumps(audit, indent=2, sort_keys=True, allow_nan=False) + "\n",
        "references.jsonl": "".join(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
            for payload in payloads
        ),
    }
    # Existing frozen records are immutable. Timing-bearing selection audit is
    # observational and also cannot silently replace the original generation.
    for name, content in files.items():
        path = directory / name
        if path.exists() and path.read_text(encoding="utf-8") != content:
            raise ValueError(f"refusing to overwrite frozen M4 artifact: {path}")
    for name, content in files.items():
        path = directory / name
        if not path.exists():
            path.write_text(content, encoding="utf-8", newline="\n")


def load_m4_references(root: Path) -> dict[str, FrozenReference]:
    directory = root / "data" / "synthetic" / "m4"
    audit = json.loads((directory / "selection_audit.json").read_text(encoding="utf-8"))
    if audit["status"] != "PASS" or audit["failed_equivalence_findings"]:
        raise ValueError("new suite equivalence failures block the performance comparison")
    historical = load_references(root)
    selected = {
        row["reference_id"]: row for row in audit["selections"] if row["status"] == "SELECTED"
    }
    references = {name: historical[name] for name in selected if name in KNOWN_DIAGNOSTICS}
    for line in (directory / "references.jsonl").read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        reference = _new_reference(raw["source_record"])
        if reference.reference_hash != raw["reference_hash"]:
            raise ValueError("new frozen reference record hash mismatch")
        if reference.name in references:
            raise ValueError("duplicate M4 reference")
        references[reference.name] = reference
    if set(references) != set(selected):
        raise ValueError("frozen M4 reference IDs differ from selected suite")
    for name, reference in references.items():
        row = selected[name]
        if (
            row["reference_hash"] != reference.reference_hash
            or row["qubo_hash"] != reference.qubo_hash
            or row["ising_hash"] != reference.ising_hash
            or row["qubit_count"] != reference.qubo.number_variables
        ):
            raise ValueError("selected M4 reference identities mismatch")
    return dict(sorted(references.items()))
