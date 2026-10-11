"""Supplemental deterministic encoding features for the original RQ-M3.6.

This measures frozen encodings, without reencoding, changing the manifest,
altering primary settings, or running any stochastic optimization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from fractions import Fraction
from pathlib import Path
from statistics import mean, median

from m3_report_analysis import hamiltonian_uniqueness


def atomic_constraints(instance, model):
    jobs = {job["id"]: job for job in instance["jobs"]}
    worker_jobs = defaultdict(list)
    for variable in model["mapping"]["variables"]:
        if variable["kind"] == "assignment":
            worker_jobs[variable["worker_id"]].append(jobs[variable["job_id"]])
    overlap = 0
    for selected in worker_jobs.values():
        for index, left in enumerate(selected):
            for right in selected[index + 1 :]:
                overlap += (
                    left["start_minute"] < right["end_minute"]
                    and right["start_minute"] < left["end_minute"]
                )
    integers = model["mapping"]["integers"]
    components = {
        "coverage": len(instance["jobs"]),
        "mandatory": sum(job["must_assign"] for job in instance["jobs"]),
        "overlap": overlap,
        "workload": sum(group["role"] == "workload_slack" for group in integers),
        "imbalance": sum(group["role"] in ("upper_slack", "lower_slack") for group in integers)
        + int(any(group["role"] == "load_range" for group in integers)),
    }
    active_types = sum(
        bool(component["linear"] or component["quadratic"] or component["constant"])
        for name, component in model["components"].items()
        if name != "business"
    )
    business = model["components"]["business"]
    maximum_business_coefficient = max(
        [abs(term["coefficient"]) for term in (*business["linear"], *business["quadratic"])],
        default=0,
    )
    penalty = model["penalties"]["applied_penalty"]
    ratio = (
        Fraction(penalty, maximum_business_coefficient) if maximum_business_coefficient else None
    )
    return {
        "atomic_encoded_constraint_components": components,
        "atomic_encoded_constraint_count": sum(components.values()),
        "active_constraint_component_type_count": active_types,
        "applied_penalty": penalty,
        "maximum_absolute_nonzero_business_polynomial_coefficient": maximum_business_coefficient
        or None,
        "penalty_to_business_coefficient_ratio_exact": str(ratio) if ratio is not None else None,
        "penalty_to_business_coefficient_ratio": float(ratio) if ratio is not None else None,
    }


def load_structure(root, manifest):
    directory = root / "data/synthetic/m2"
    frozen = {
        row["name"]: row
        for row in map(
            json.loads, (directory / "frozen_qubos.jsonl").read_text(encoding="utf-8").splitlines()
        )
    }
    random_inputs = json.loads((directory / "random_cases.json").read_text(encoding="utf-8"))
    records = []
    for entry in manifest["primary_suite"]:
        name = entry["reference_id"]
        instance = (
            random_inputs[name]["instance"]
            if name.startswith("random-")
            else json.loads((directory / "hand_cases" / f"{name}.json").read_text(encoding="utf-8"))
        )
        row = frozen[name]
        ising = row["ising_model"]
        signature = {
            key: ising[key]
            for key in (
                "number_variables",
                "energy_denominator",
                "constant4",
                "fields",
                "couplings",
            )
        }
        digest = hashlib.sha256(
            json.dumps(signature, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        records.append(
            {
                "reference_name": name,
                "instance_id": entry["instance_id"],
                "split": entry["split"],
                "number_qubits": entry["qubit_count"],
                "indexed_hamiltonian_sha256": digest,
                **atomic_constraints(instance, row["qubo_model"]),
            }
        )
    return records


def pearson(pairs):
    if len(pairs) < 3:
        return None
    xs, ys = zip(*pairs, strict=True)
    xm, ym = mean(xs), mean(ys)
    denominator = math.sqrt(sum((x - xm) ** 2 for x in xs) * sum((y - ym) ** 2 for y in ys))
    return sum((x - xm) * (y - ym) for x, y in pairs) / denominator if denominator else None


def correlations(records, runs):
    groups = defaultdict(list)
    for run in runs:
        groups[run["reference_name"], run["p"]].append(run)
    results = []
    for split in ("development", "evaluation"):
        for p in (1, 2, 3):
            for feature in (
                "atomic_encoded_constraint_count",
                "active_constraint_component_type_count",
                "penalty_to_business_coefficient_ratio",
            ):
                for outcome in (
                    "ground_state_probability",
                    "valid_probability",
                    "normalized_energy_error",
                ):
                    rows = [
                        record
                        for record in records
                        if record["split"] == split and record[feature] is not None
                    ]
                    pairs = [
                        (
                            float(record[feature]),
                            median(run[outcome] for run in groups[record["reference_name"], p]),
                        )
                        for record in rows
                    ]
                    results.append(
                        {
                            "split": split,
                            "p": p,
                            "feature": feature,
                            "outcome": outcome,
                            "reference_count": len(rows),
                            "distinct_indexed_hamiltonian_count": len(
                                {record["indexed_hamiltonian_sha256"] for record in rows}
                            ),
                            "pearson_r": pearson(pairs),
                        }
                    )
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--describe",
        action="store_true",
        help="Print frozen features only, without reading outcomes or saving output",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = json.loads(
        (root / "data/synthetic/m3/benchmark_manifest.json").read_text(encoding="utf-8")
    )
    structure = load_structure(root, manifest)
    fixtures = {
        row["reference_name"]: row["atomic_encoded_constraint_count"]
        for row in structure
        if row["reference_name"] in ("A_single_job", "H_workload", "M_active_imbalance")
    }
    assert fixtures == {"A_single_job": 2, "H_workload": 3, "M_active_imbalance": 6}
    if args.describe:
        print(
            json.dumps(
                {
                    "fixture_checks": fixtures,
                    "records": structure,
                    "hamiltonian_uniqueness": hamiltonian_uniqueness(root, manifest),
                },
                ensure_ascii=True,
            )
        )
        return
    runs = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((root / "results/m3/runs").glob("*.json"))
    ]
    expected = {
        (entry["reference_id"], p, seed)
        for entry in manifest["primary_suite"]
        for p in manifest["configuration"]["p_values"]
        for seed in manifest["configuration"]["restart_seeds"]
    }
    identities = [
        (run.get("reference_name"), run.get("p"), run.get("initialization_seed")) for run in runs
    ]
    if (
        len(identities) != len(expected)
        or set(identities) != expected
        or any(
            run.get("status") != "COMPLETE"
            or run.get("manifest_content_sha256") != manifest["manifest_content_sha256"]
            for run in runs
        )
    ):
        raise SystemExit(
            "Supplementary outcome analysis requires all 660 complete frozen identities"
        )
    result = {
        "analysis": (
            "Supplemental deterministic structural measurement for prespecified RQ-M3.6; "
            "no new optimization, no changes to selection/configuration and no outcome filtering."
        ),
        "manifest_content_sha256": manifest["manifest_content_sha256"],
        "atomic_count_definition": {
            "coverage": "One squared coverage equation per job.",
            "mandatory": "One omission penalty per must_assign job.",
            "overlap": (
                "One eligible overlapping unordered job-pair penalty per worker, "
                "using strict half-open interval intersection."
            ),
            "workload": (
                "One squared workload equation per stored workload_slack IntegerEncoding group, "
                "regardless of bit-group length."
            ),
            "imbalance": (
                "One equation per upper_slack group, one per lower_slack group, plus A-B-D=0 "
                "when a load_range group exists."
            ),
            "scope": (
                "Counts atomic encoded penalty terms, not original skill/availability predicates "
                "already enforced through frozen pruning. A=2 includes its mandatory penalty; "
                "H=3 and M=6."
            ),
        },
        "penalty_ratio_definition": (
            "Applied common hard-constraint penalty P divided by maximum absolute nonzero "
            "coefficient of the frozen business polynomial, excluding the constant. "
            "Exact Fraction; "
            "null for an identically coefficient-free business polynomial. Descriptive scale "
            "feature only; no Hamiltonian rescaling."
        ),
        "records": structure,
        "hamiltonian_uniqueness": hamiltonian_uniqueness(root, manifest),
        "correlation_unit": (
            "One median across the ten fixed restarts per original reference and depth, "
            "stratified by split. All references retained; evaluation has 19 references "
            "but 17 distinct indexed Hamiltonians."
        ),
        "correlations": correlations(structure, runs),
        "limits": (
            "Small, structurally selected, confounded synthetic suite. Duplicate Hamiltonians "
            "and repeated depths prevent an independent-sample interpretation. No causal claim, "
            "significance test or retrospective hypothesis."
        ),
    }
    output = root / "results/m3/constraint_structure_analysis.json"
    output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "fixture_checks": fixtures,
                "reference_count": len(structure),
                "distinct_indexed_hamiltonian_count": result["hamiltonian_uniqueness"][
                    "distinct_indexed_hamiltonian_count"
                ],
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
