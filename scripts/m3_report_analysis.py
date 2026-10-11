"""Descriptive completion-report analysis of the fixed M3 study; no tuning."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median, stdev

METRICS = (
    "ground_state_probability",
    "valid_probability",
    "encoded_valid_probability",
    "decoded_optimal_schedule_probability",
    "normalized_energy_error",
    "energy_residual",
    "ground_probability_lift",
    "final_expected_energy",
    "runtime_ms",
    "nfev",
)


def distribution(values):
    return {
        "mean": mean(values),
        "median": median(values),
        "sample_standard_deviation": stdev(values) if len(values) > 1 else 0.0,
        "minimum": min(values),
        "maximum": max(values),
    }


def summary(rows):
    return {
        "run_count": len(rows),
        "instance_count": len({row["reference_name"] for row in rows}),
        "metrics": {key: distribution([row[key] for row in rows]) for key in METRICS},
        "optimizer_converged": sum(row["success"] for row in rows),
        "optimizer_budget_terminations": sum(
            "MAXFUN" in row["termination_message"] for row in rows
        ),
        "ground_probability_gt_half": sum(row["ground_state_probability"] > 0.5 for row in rows),
        "ground_probability_below_uniform": sum(
            row["ground_state_probability"] < row["uniform_ground_probability"] - 1e-12
            for row in rows
        ),
        "ground_probability_above_uniform": sum(
            row["ground_state_probability"] > row["uniform_ground_probability"] + 1e-12
            for row in rows
        ),
        "sum_optimizer_evaluations": sum(row["nfev"] for row in rows),
        "sum_run_runtime_seconds": sum(row["runtime_ms"] for row in rows) / 1000,
    }


def grouped(rows, key):
    groups = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    return groups


def hamiltonian_uniqueness(root, manifest):
    frozen = {
        row["name"]: row
        for row in map(
            json.loads,
            (root / "data/synthetic/m2/frozen_qubos.jsonl")
            .read_text(encoding="utf-8")
            .splitlines(),
        )
    }
    groups = defaultdict(list)
    signatures = {}
    for entry in manifest["primary_suite"]:
        ising = frozen[entry["reference_id"]]["ising_model"]
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
        groups[digest].append({"reference_name": entry["reference_id"], "split": entry["split"]})
        signatures[digest] = signature
    return {
        "definition": (
            "Exact indexed Hamiltonian identity: qubit count, energy denominator, constant, "
            "fields and couplings; scheduling mapping and provenance identities excluded. "
            "No qubit-permutation equivalence reduction."
        ),
        "reference_count": len(manifest["primary_suite"]),
        "distinct_indexed_hamiltonian_count": len(groups),
        "by_split": {
            split: {
                "reference_count": sum(
                    entry["split"] == split for entry in manifest["primary_suite"]
                ),
                "distinct_indexed_hamiltonian_count": sum(
                    any(entry["split"] == split for entry in group) for group in groups.values()
                ),
            }
            for split in ("development", "evaluation")
        },
        "groups": [
            {
                "indexed_hamiltonian_sha256": digest,
                "number_qubits": signatures[digest]["number_variables"],
                "references": members,
            }
            for digest, members in sorted(groups.items())
        ],
        "duplicate_groups": [
            {"indexed_hamiltonian_sha256": digest, "references": members}
            for digest, members in sorted(groups.items())
            if len(members) > 1
        ],
        "interpretation": (
            "Every frozen reference and restart is retained. Evaluation has 19 references "
            "but only 17 distinct indexed Hamiltonians; these are not 19 independent "
            "quantum optimization problems."
        ),
    }


def analyze(root, runs, manifest):
    restart_groups = grouped(runs, lambda row: (row["reference_name"], row["p"]))
    group_summaries = []
    for (name, p), rows in sorted(restart_groups.items()):
        item = summary(rows)
        item.update(
            {
                "reference_name": name,
                "p": p,
                "qubits": rows[0]["qubit_count"],
                "split": rows[0]["split"],
            }
        )
        item["ground_probability_range"] = max(
            row["ground_state_probability"] for row in rows
        ) - min(row["ground_state_probability"] for row in rows)
        item["normalized_energy_error_range"] = max(
            row["normalized_energy_error"] for row in rows
        ) - min(row["normalized_energy_error"] for row in rows)
        item["below_uniform_restart_median"] = (
            item["metrics"]["ground_state_probability"]["median"]
            < rows[0]["uniform_ground_probability"] - 1e-12
        )
        item["uniform_ground_probability"] = rows[0]["uniform_ground_probability"]
        item["logical_circuit"] = rows[0]["circuit"]
        group_summaries.append(item)
    paired = []
    medians = {(row["reference_name"], row["p"]): row for row in group_summaries}
    for first, second in ((1, 2), (2, 3), (1, 3)):
        for metric, direction in (
            ("ground_state_probability", 1),
            ("valid_probability", 1),
            ("normalized_energy_error", -1),
        ):
            pairs = [
                {
                    "reference_name": name,
                    "from_median": medians[name, first]["metrics"][metric]["median"],
                    "to_median": medians[name, second]["metrics"][metric]["median"],
                }
                for name in sorted({row["reference_name"] for row in runs})
                if (name, first) in medians and (name, second) in medians
            ]
            if not pairs:
                continue
            changes = [row["to_median"] - row["from_median"] for row in pairs]
            paired.append(
                {
                    "from_p": first,
                    "to_p": second,
                    "metric": metric,
                    "instance_count": len(pairs),
                    "change_to_minus_from": distribution(changes),
                    "comparison_absolute_tolerance": 1e-9,
                    "improved_instances": sum(direction * change > 1e-9 for change in changes),
                    "worsened_instances": sum(direction * change < -1e-9 for change in changes),
                    "ties_within_1e_9": sum(abs(change) <= 1e-9 for change in changes),
                    "pairs": pairs,
                }
            )
    sampling = [row["sampling"] for row in runs]
    result = {
        "analysis": (
            "Descriptive fixed-study reporting, with every restart retained; "
            "no parameter tuning or outcome filtering."
        ),
        "manifest_content_sha256": manifest["manifest_content_sha256"],
        "source_commits": sorted({row["environment"]["git_commit"] for row in runs}),
        "all_source_checkouts_clean": all(row["environment"]["git_dirty"] is False for row in runs),
        "hamiltonian_uniqueness": hamiltonian_uniqueness(root, manifest),
        "overall": summary(runs),
        "per_depth": {
            str(p): summary(rows) for p, rows in sorted(grouped(runs, lambda row: row["p"]).items())
        },
        "per_split": {
            split: summary(rows)
            for split, rows in sorted(grouped(runs, lambda row: row["split"]).items())
        },
        "per_split_depth": {
            f"{split}/p{p}": summary(rows)
            for (split, p), rows in sorted(
                grouped(runs, lambda row: (row["split"], row["p"])).items()
            )
        },
        "per_qubits_depth": {
            f"n{qubits}/p{p}": summary(rows)
            for (qubits, p), rows in sorted(
                grouped(runs, lambda row: (row["qubit_count"], row["p"])).items()
            )
        },
        "restart_groups": group_summaries,
        "paired_depth_restart_medians": paired,
        "largest_ground_probability_seed_spreads": sorted(
            group_summaries, key=lambda row: row["ground_probability_range"], reverse=True
        )[:10],
        "groups_with_restart_median_below_uniform": [
            {
                "reference_name": row["reference_name"],
                "p": row["p"],
                "median_ground_probability": row["metrics"]["ground_state_probability"]["median"],
                "uniform_ground_probability": row["uniform_ground_probability"],
            }
            for row in group_summaries
            if row["below_uniform_restart_median"]
        ],
        "termination_message_counts": dict(Counter(row["termination_message"] for row in runs)),
        "correctness": {
            "maximum_statevector_expectation_absolute_difference": max(
                row["statevector_expectation_absolute_difference"] for row in runs
            ),
            "maximum_ground_vs_energy_consistent_optimum_mass_difference": max(
                abs(row["ground_state_probability"] - row["optimal_valid_probability"])
                for row in runs
            ),
            "runs_with_raw_decoded_optimum_mass_larger_than_ground": sum(
                row["decoded_optimal_schedule_probability"]
                > row["ground_state_probability"] + 1e-12
                for row in runs
            ),
            "minimum_ground_binomial_two_sided_pvalue": min(
                row["binomial_two_sided_pvalue"] for row in sampling
            ),
            "maximum_ground_frequency_absolute_difference": max(
                row["ground_frequency_absolute_difference"] for row in sampling
            ),
            "sampling_verified_runs": sum(row["sampling_verified"] for row in sampling),
            "runs_observing_ground": sum(row["ground_state_observed"] for row in sampling),
            "runs_observing_decoded_optimal_schedule": sum(
                row["decoded_optimal_schedule_observed"] for row in sampling
            ),
            "sum_shots": sum(row["shots"] for row in sampling),
        },
        "interpretation_limits": (
            "Tiny, structurally selected synthetic instances; ideal classical simulation; "
            "depth comparisons use paired instance restart-medians. Size and encoding "
            "features are confounded. No generalization, causality or quantum advantage "
            "is inferred."
        ),
    }
    correlation_path = root / "results/m3/summaries/exploratory_correlations.json"
    if correlation_path.exists():
        correlations = json.loads(correlation_path.read_text(encoding="utf-8"))
        result["exploratory_correlations"] = correlations
        result["largest_evaluation_associations_by_depth"] = {
            str(p): sorted(
                [
                    row
                    for row in correlations["records"]
                    if row["split"] == "evaluation"
                    and row["p"] == p
                    and row["pearson_r"] is not None
                ],
                key=lambda row: abs(row["pearson_r"]),
                reverse=True,
            )[:10]
            for p in (1, 2, 3)
        }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--partial", action="store_true", help="Print progress only; never persist partial analysis"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = json.loads(
        (root / "data/synthetic/m3/benchmark_manifest.json").read_text(encoding="utf-8")
    )
    expected = {
        (row["reference_id"], p, seed)
        for row in manifest["primary_suite"]
        for p in manifest["configuration"]["p_values"]
        for seed in manifest["configuration"]["restart_seeds"]
    }
    runs = []
    malformed = []
    for path in sorted((root / "results/m3/runs").glob("*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if row.get("status") != "COMPLETE":
                raise ValueError("run is not COMPLETE")
            if row["manifest_content_sha256"] != manifest["manifest_content_sha256"]:
                raise ValueError("run has a different manifest hash")
            runs.append(row)
        except (ValueError, KeyError) as error:
            malformed.append({"path": str(path), "error": str(error)})
    identities = [(row["reference_name"], row["p"], row["initialization_seed"]) for row in runs]
    complete = not malformed and len(identities) == len(expected) and set(identities) == expected
    progress = {
        "complete": complete,
        "expected_runs": len(expected),
        "complete_runs": len(runs),
        "malformed_or_incomplete": malformed,
    }
    if args.partial:
        print(json.dumps(progress, ensure_ascii=True))
        return
    if not complete:
        raise SystemExit(
            "Refusing final analysis before complete fixed study: " + json.dumps(progress)
        )
    for row in runs:
        for key in METRICS:
            if not math.isfinite(row[key]):
                raise ValueError(f"Nonfinite {key} in {row['run_id']}")
    result = {**progress, **analyze(root, runs, manifest)}
    output = args.output or root / "results/m3/report_analysis.json"
    output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "run_count": len(runs),
                "overall": result["overall"],
                "correctness": result["correctness"],
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
