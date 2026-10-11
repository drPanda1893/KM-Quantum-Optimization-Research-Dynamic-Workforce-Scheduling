"""Prespecified structural suite, protected references and honest restart summaries."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from fractions import Fraction
from pathlib import Path
from statistics import mean, median, stdev
from typing import Any

from km_quantum.qubo.models import QUBOModel

MANIFEST_PATH = Path("data/synthetic/m3/benchmark_manifest.json")
PROVENANCE_PATH = Path("data/synthetic/m3/provenance.json")
FROZEN_REFERENCES_PATH = Path("data/synthetic/m2/frozen_qubos.jsonl")
PRIMARY_CONFIG: dict[str, Any] = {
    "p_values": [1, 2, 3],
    "restart_seeds": list(range(10)),
    "optimizer": "COBYLA",
    "optimizer_config": {"maxiter": 300, "tol": 1e-6, "rhobeg": 0.5},
    "initialization": "numpy.default_rng(seed).uniform(0,pi,actual_parameter_count)",
    "shots": 1024,
    "sampler_seed_rule": "10000 + initialization_seed + 100 * p",
    "numerical_tolerances": {"atol": 1e-9, "rtol": 1e-12},
    "ground_probability_success_threshold": 0.5,
    "ground_probability_success_comparison": "strictly greater than",
    "hamiltonian_scaling": "none; unmodified M2 coefficients",
    "max_qubits": 14,
}


def _canonical_hash(value: object) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )


def verify_provenance(root: str | Path) -> dict[str, Any]:
    """Verify M1/M2 content against the hashes captured before M3 edits.

    Dependency declarations, README and CLI dispatch have separate baseline hashes
    because M3 intentionally adds dependencies and commands. They do not change
    the mathematical reference. Initial byte hashes remain untouched; a derived
    LF hash permits only CRLF/LF checkout differences. Missing files or any other
    content changes still fail the gate.
    """
    root = Path(root)
    provenance = json.loads((root / PROVENANCE_PATH).read_text(encoding="utf-8"))
    protected: dict[str, str] = provenance["protected_files_sha256"]
    canonical_lf: dict[str, str] = provenance.get("protected_files_lf_sha256", {})
    changed = []
    line_endings = []
    for relative, expected in sorted(protected.items()):
        path = root / relative
        actual = _file_hash(path) if path.is_file() else None
        if actual != expected:
            normalized = (
                hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
                if path.is_file()
                else None
            )
            diagnostic = {
                "path": relative,
                "expected_sha256": expected,
                "actual_sha256": actual,
            }
            if normalized is not None and normalized == canonical_lf.get(relative):
                line_endings.append(diagnostic)
            else:
                changed.append(diagnostic)
    acceptance = root / "results/m2-acceptance.json"
    expected_acceptance = provenance.get("reconstructed_acceptance_report_sha256")
    acceptance_unchanged = expected_acceptance is None or (
        acceptance.is_file() and _file_hash(acceptance) == expected_acceptance
    )
    return {
        "unchanged": not changed and acceptance_unchanged,
        "mathematical_unchanged": not changed,
        "protected_bytes_unchanged": not changed and not line_endings,
        "line_ending_only_changes": line_endings,
        "count": len(protected),
        "changed": changed,
        "m2_reference_commit": provenance["m2_reference_commit"],
        "reconstructed_acceptance_unchanged": acceptance_unchanged,
    }


def create_manifest(root: str | Path) -> dict[str, Any]:
    """Write an immutable deterministic manifest without executing quantum code.

    The primary study contains all hand cases at <=14 bits plus one random case
    for every available qubit count, selected by minimum SHA-256(instance_id).
    Every eligible reference participates in correctness gates, even when it is
    outside this compact primary quality study. Regeneration must match exactly.
    """
    root = Path(root)
    if not verify_provenance(root)["unchanged"]:
        raise ValueError("protected M1/M2 artifacts changed; refusing to freeze M3 selection")
    source = root / FROZEN_REFERENCES_PATH
    # The manifest identifies the original frozen byte snapshot. Its recorded
    # SHA remains stable when Git checks out equivalent LF text on another host.
    provenance = json.loads((root / PROVENANCE_PATH).read_text(encoding="utf-8"))
    source_hash = provenance["protected_files_sha256"][FROZEN_REFERENCES_PATH.as_posix()]
    entries: list[dict[str, Any]] = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        raw = json.loads(line)
        model = QUBOModel.model_validate(raw["qubo_model"])
        identifier = model.mapping.instance_id
        identifier_hash = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
        entry = {
            "reference_id": raw["name"],
            "instance_id": identifier,
            "instance_id_sha256": identifier_hash,
            "reference_sha256": _canonical_hash(raw),
            "qubo_sha256": raw["qubo_hash"],
            "ising_sha256": raw["ising_hash"],
            "source_path": FROZEN_REFERENCES_PATH.as_posix(),
            "source_line": line_number,
            "source_sha256": source_hash,
            "qubit_count": model.number_variables,
            "ground_state_count": len(raw["ground_bits"]),
            "ground_energy": raw["ground_energy"],
            "constraint_energy_margin": raw["constraint_energy_margin"],
            "structural_metadata": {
                **model.metrics().model_dump(mode="json"),
                "safe_penalty": model.penalties.safe_penalty,
                "applied_penalty": model.penalties.applied_penalty,
                "business_upper_bound": model.penalties.business_upper_bound,
                "integer_group_count": len(model.mapping.integers),
                "active_constraint_component_count": sum(
                    bool(poly.linear or poly.quadratic or poly.constant)
                    for name, poly in model.components.items()
                    if name != "business"
                ),
            },
            "split": "development" if int(identifier_hash, 16) % 5 == 0 else "evaluation",
            "family": "random" if raw["name"].startswith("random-") else "hand",
            "eligible": model.number_variables <= PRIMARY_CONFIG["max_qubits"],
        }
        entries.append(entry)
    eligible = [entry for entry in entries if entry["eligible"]]
    random_by_size: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for entry in eligible:
        if entry["family"] == "random":
            random_by_size[entry["qubit_count"]].append(entry)
    selected_ids = {entry["reference_id"] for entry in eligible if entry["family"] == "hand"} | {
        min(group, key=lambda entry: entry["instance_id_sha256"])["reference_id"]
        for group in random_by_size.values()
    }
    selected = [entry for entry in eligible if entry["reference_id"] in selected_ids]
    manifest = {
        "schema_version": 1,
        "milestone": "M3",
        "m2_reference_commit": verify_provenance(root)["m2_reference_commit"],
        "frozen_reference_file_sha256": source_hash,
        "selection_rule": (
            "All hand references with n<=14, plus one random reference per available n<=14 "
            "chosen by smallest SHA-256(instance_id); selection uses no QAOA outcomes."
        ),
        "split_rule": "int(SHA-256(instance_id),16) modulo 5 == 0: development; else evaluation",
        "resource_rationale": (
            "A 14-qubit complex128 statevector requires 262144 bytes for amplitudes alone; "
            "the conservative cap avoids exponential growth. A fixed structural subset "
            "bounds 10-restart p=1,2,3 optimizer work before quality is observed."
        ),
        "configuration": PRIMARY_CONFIG,
        "reference_count": len(entries),
        "eligible_reference_count": len(eligible),
        "primary_reference_count": len(selected),
        "planned_primary_runs": len(selected) * 3 * 10,
        "correctness_reference_ids": [entry["reference_id"] for entry in eligible],
        "primary_suite": selected,
        "excluded_references": [
            {**entry, "reason": "qubit count exceeds prespecified 14-qubit cap"}
            for entry in entries
            if not entry["eligible"]
        ],
        "eligible_not_selected": [
            {**entry, "reason": "outside fixed per-size primary random sample; included in gates"}
            for entry in eligible
            if entry["reference_id"] not in selected_ids
        ],
    }
    manifest["manifest_content_sha256"] = _canonical_hash(manifest)
    path = root / MANIFEST_PATH
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != manifest:
            raise ValueError("M3 manifest is already frozen with different content")
    else:
        _write_json(path, manifest)
    return manifest


def _metric(run: dict[str, Any], key: str) -> Any:
    if key in run:
        return run[key]
    for section in ("energy", "probability", "circuit", "optimization", "sampling"):
        values = run.get(section)
        if isinstance(values, dict) and key in values:
            return values[key]
    raise ValueError(f"run {run.get('instance_id')} is missing required metric {key}")


def _distribution(values: list[float]) -> dict[str, float]:
    return {
        "mean": mean(values),
        "median": median(values),
        "standard_deviation": stdev(values) if len(values) > 1 else 0.0,
        "minimum": min(values),
        "maximum": max(values),
    }


def aggregate_runs(runs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Report every restart, with sample deviation (ddof=1), grouped by instance/p.

    An optimizer budget termination remains in every quality distribution; it is
    counted as nonconverged rather than silently discarded. Duplicate restart
    identities are rejected to prevent accidental weighting of resumed runs.
    """
    groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    identities: set[tuple[str, int, int]] = set()
    for run in runs:
        identity = (run["instance_id"], run["p"], run["initialization_seed"])
        if identity in identities:
            raise ValueError(f"duplicate restart identity: {identity}")
        identities.add(identity)
        groups[identity[:2]].append(run)
    result = []
    for (identifier, p), group in sorted(groups.items()):
        fields = {
            key: [float(_metric(run, key)) for run in group]
            for key in (
                "final_expected_energy",
                "normalized_energy_error",
                "ground_state_probability",
                "valid_probability",
                "runtime_ms",
                "nfev",
            )
        }
        if any(not math.isfinite(value) for values in fields.values() for value in values):
            raise ValueError(
                "aggregate metrics must be finite; failed attempts need explicit records"
            )
        energies = fields["final_expected_energy"]
        grounds = fields["ground_state_probability"]
        result.append(
            {
                "instance_id": identifier,
                "p": p,
                "number_runs": len(group),
                "initialization_seeds": sorted(run["initialization_seed"] for run in group),
                "qubit_count": group[0]["qubit_count"],
                "split": group[0].get("split"),
                "mean_final_expected_energy": mean(energies),
                "median_final_expected_energy": median(energies),
                "standard_deviation_final_expected_energy": (
                    stdev(energies) if len(energies) > 1 else 0.0
                ),
                "best_final_expected_energy": min(energies),
                "worst_final_expected_energy": max(energies),
                "median_normalized_energy_error": median(fields["normalized_energy_error"]),
                "mean_ground_state_probability": mean(grounds),
                "median_ground_state_probability": median(grounds),
                "ground_probability_success_threshold": PRIMARY_CONFIG[
                    "ground_probability_success_threshold"
                ],
                "ground_probability_success_rate": mean(
                    float(value > PRIMARY_CONFIG["ground_probability_success_threshold"])
                    for value in grounds
                ),
                "optimizer_convergence_rate": mean(
                    float(bool(_metric(run, "success"))) for run in group
                ),
                "distributions": {key: _distribution(values) for key, values in fields.items()},
            }
        )
    return result


def exploratory_correlations(
    summaries: Sequence[dict[str, Any]], manifest: dict[str, Any]
) -> dict[str, Any]:
    """Pearson associations using one median per instance/depth, never per restart.

    Results are descriptive, split and depth stratified, and not causal. Constant
    inputs produce null rather than invented correlations. No p-value or post hoc
    hypothesis is reported for this small dependent synthetic suite.
    """
    references = {entry["instance_id"]: entry for entry in manifest["primary_suite"]}
    features = (
        "qubit_count",
        "constraint_energy_margin",
        "qubo_density",
        "coefficient_dynamic_range",
        "slack_variable_count",
        "assignment_variable_count",
        "preprocessing_reduction",
        "active_constraint_component_count",
    )
    outcomes = {
        "P_ground": "ground_state_probability",
        "P_valid": "valid_probability",
        "energy_error": "normalized_energy_error",
        "convergence_rate": None,
    }
    records = []
    for split in ("development", "evaluation"):
        for p in PRIMARY_CONFIG["p_values"]:
            rows = [
                row
                for row in summaries
                if row["p"] == p and references[row["instance_id"]]["split"] == split
            ]
            for feature in features:
                for label, metric in outcomes.items():
                    pairs = []
                    for row in rows:
                        reference = references[row["instance_id"]]
                        value = reference.get(
                            feature, reference["structural_metadata"].get(feature)
                        )
                        if value is None:
                            continue
                        x = float(Fraction(value)) if isinstance(value, str) else float(value)
                        y = (
                            row["optimizer_convergence_rate"]
                            if metric is None
                            else row["distributions"][metric]["median"]
                        )
                        pairs.append((x, float(y)))
                    coefficient = None
                    if len(pairs) >= 3:
                        xs, ys = zip(*pairs, strict=True)
                        xm, ym = mean(xs), mean(ys)
                        denominator = math.sqrt(
                            sum((x - xm) ** 2 for x in xs) * sum((y - ym) ** 2 for y in ys)
                        )
                        if denominator:
                            coefficient = sum((x - xm) * (y - ym) for x, y in pairs) / denominator
                    records.append(
                        {
                            "split": split,
                            "p": p,
                            "feature": feature,
                            "outcome": label,
                            "instance_count": len(pairs),
                            "pearson_r": coefficient,
                        }
                    )
    return {
        "analysis": "exploratory associations; no causal interpretation or confirmatory hypothesis",
        "warning": (
            "Small structurally selected synthetic suite; features are confounded and instances "
            "recur across depths. Correlations are unstable and cannot establish generalization."
        ),
        "unit_of_analysis": "one restart-median per instance/p, stratified by split and p",
        "records": records,
    }


def create_plots(
    runs: Sequence[dict[str, Any]],
    trace_paths: Sequence[str | Path],
    output: str | Path,
) -> list[str]:
    """Create five reproducible figures from saved raw metrics and full traces."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not runs:
        raise ValueError("plots require at least one run")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    summaries = aggregate_runs(runs)
    paths = []

    def save(figure: Any, name: str) -> None:
        figure.tight_layout()
        path = output / f"{name}.png"
        figure.savefig(path, dpi=160, metadata={"Software": "KM Quantum M3"})
        plt.close(figure)
        paths.append(str(path))

    fig, ax = plt.subplots(figsize=(8, 5))
    for path in trace_paths:
        path = Path(path)
        trace = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if trace:
            ax.plot(
                [row["evaluation"] for row in trace],
                [row["expected_energy"] for row in trace],
                alpha=0.15,
                linewidth=0.7,
            )
    ax.set(xlabel="Optimizer evaluation", ylabel="Expected energy (M2 units)")
    ax.set_title("Complete optimizer trajectories (all restarts)")
    save(fig, "convergence")

    fig, ax = plt.subplots(figsize=(8, 5))
    for identifier in sorted({row["instance_id"] for row in summaries}):
        rows = [row for row in summaries if row["instance_id"] == identifier]
        ax.plot(
            [row["p"] for row in rows],
            [row["median_ground_state_probability"] for row in rows],
            marker="o",
            alpha=0.5,
        )
    ax.set(xlabel="QAOA depth p", ylabel="Median P_ground across restarts", xticks=[1, 2, 3])
    ax.set_ylim(-0.02, 1.02)
    ax.set_title("Depth comparison; one line per instance")
    save(fig, "ground_probability_vs_p")

    for metric, ylabel, name in (
        (
            "ground_state_probability",
            "Median P_ground across restarts",
            "ground_probability_vs_qubits",
        ),
        ("valid_probability", "Median P_valid across restarts", "valid_probability_vs_qubits"),
    ):
        fig, ax = plt.subplots(figsize=(8, 5))
        for p in PRIMARY_CONFIG["p_values"]:
            rows = [row for row in summaries if row["p"] == p]
            ax.scatter(
                [row["qubit_count"] for row in rows],
                [row["distributions"][metric]["median"] for row in rows],
                label=f"p={p}",
                alpha=0.7,
            )
        ax.set(xlabel="Qubit count", ylabel=ylabel, ylim=(-0.02, 1.02))
        ax.legend()
        ax.set_title("Structural suite; all instance/depth restart medians")
        save(fig, name)

    fig, ax = plt.subplots(figsize=(8, 5))
    by_group = {(row["instance_id"], row["p"]): row for row in summaries}
    baselines = {
        (run["instance_id"], run["p"]): float(_metric(run, "uniform_ground_probability"))
        for run in runs
    }
    for p in PRIMARY_CONFIG["p_values"]:
        keys = [key for key in by_group if key[1] == p]
        ax.scatter(
            [baselines[key] for key in keys],
            [by_group[key]["median_ground_state_probability"] for key in keys],
            label=f"p={p}",
            alpha=0.7,
        )
    ax.plot([0, 1], [0, 1], "k--", linewidth=1, label="Uniform equality")
    ax.set(xlabel="Uniform P_ground", ylabel="Median QAOA P_ground", xlim=(0, 1), ylim=(0, 1))
    ax.set_title("Probability concentration relative to uniform sampling")
    ax.legend()
    save(fig, "qaoa_vs_uniform")
    return paths


def _verify_m2_acceptance(root: Path, references: dict[str, Any]) -> dict[str, Any]:
    provenance = json.loads((root / PROVENANCE_PATH).read_text(encoding="utf-8"))
    path = root / "results/m2-acceptance.json"
    if not path.is_file() or _file_hash(path) != provenance.get(
        "reconstructed_acceptance_report_sha256"
    ):
        raise ValueError("M2 reconstructed acceptance report is missing or changed")
    report = json.loads(path.read_text(encoding="utf-8"))
    if (
        report.get("status") != "PASS"
        or report.get("m2_equivalence") != "PASS"
        or report.get("feasible_suite_case_count") != 114
        or report.get("successful_feasible_cases") != 114
        or report.get("hand_case_count") != 14
        or report.get("random_case_count") != 100
    ):
        raise ValueError("M2 acceptance must prove all 114 frozen safe references")
    cases = {case["name"]: case for case in report["cases"]}
    for name, reference in references.items():
        case = cases.get(name)
        if (
            case is None
            or case.get("status") != "PASS"
            or case.get("classical_objective") != reference.ground_energy
            or case.get("ground_energy") != reference.ground_energy
            or case.get("ground_state_count") != len(reference.ground_bits)
            or case.get("invalid_ground_state_count") != 0
            or not case.get("ising_equivalence_passed")
        ):
            raise ValueError(f"M2 acceptance does not match frozen reference {name}")
    return {
        "passed": True,
        "safe_reference_count": 114,
        "report_sha256": _file_hash(path),
        "source": "reconstructed with unchanged M2 acceptance logic",
    }


def _full_gate(
    root: Path,
    output: Path,
    manifest: dict[str, Any],
    progress: Callable[[str], None] | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    from km_quantum.qaoa.analysis import build_state_table
    from km_quantum.qaoa.operators import verify_operator
    from km_quantum.qaoa.references import load_references
    from km_quantum.qaoa.simulation import environment_metadata

    def notify(message: str) -> None:
        if progress is not None:
            progress(message)

    provenance = verify_provenance(root)
    if not provenance["unchanged"]:
        raise ValueError("M1/M2 protected provenance gate failed")
    references = load_references(root)
    m2_gate = _verify_m2_acceptance(root, references)
    tables = {}
    reports = []
    # All eligible operator/state gates finish before returning any quality work.
    for index, name in enumerate(manifest["correctness_reference_ids"], 1):
        reference = references[name]
        verification = dict(verify_operator(reference.ising, PRIMARY_CONFIG["max_qubits"]))
        table = build_state_table(reference, max_qubits=PRIMARY_CONFIG["max_qubits"])
        record = {
            **verification,
            "reference_id": name,
            "instance_id": reference.instance.instance_id,
            "reference_sha256": reference.reference_hash,
            "ising_hash": reference.ising_hash,
            "ground_state_count": len(reference.ground_bits),
            "state_table_checked_ground_set": table.checked_ground_set,
        }
        reports.append(record)
        tables[name] = table
        _VALIDATION_TABLE_CACHE[reference.reference_hash] = table
        if not verification["passed"] or not table.checked_ground_set:
            _write_json(output / "operator_gate.json", {"passed": False, "references": reports})
            raise ValueError(f"complete operator/state-table gate failed for {name}")
        if index == 1 or index % 10 == 0 or index == len(manifest["correctness_reference_ids"]):
            notify(f"M3 correctness gates: {index}/{len(manifest['correctness_reference_ids'])}")
    gate = {
        "passed": True,
        "reference_count": len(reports),
        "checked_states": sum(record["checked_states"] for record in reports),
        "mismatch_count": sum(record["mismatch_count"] for record in reports),
        "max_abs_difference": max(record["max_abs_difference"] for record in reports),
        "m2_acceptance": m2_gate,
        "provenance": provenance,
        "manifest_content_sha256": manifest["manifest_content_sha256"],
        "environment": environment_metadata(root),
        "references": reports,
    }
    _write_json(output / "operator_gate.json", gate)
    return gate, references, tables


def _expected_config(p: int, seed: int) -> dict[str, Any]:
    from km_quantum.qaoa.models import QAOAConfig

    return QAOAConfig(
        p=p,
        initialization_seed=seed,
        maxiter=PRIMARY_CONFIG["optimizer_config"]["maxiter"],
        tolerance=PRIMARY_CONFIG["optimizer_config"]["tol"],
        rhobeg=PRIMARY_CONFIG["optimizer_config"]["rhobeg"],
        shots=PRIMARY_CONFIG["shots"],
        sampler_seed=10000 + seed + 100 * p,
        max_qubits=PRIMARY_CONFIG["max_qubits"],
    ).model_dump(mode="json")


_VALIDATION_TABLE_CACHE: dict[str, Any] = {}


def _assert_record_matches(expected: Any, observed: Any, location: str) -> None:
    """Compare recomputed scientific metrics, retaining strict discrete identity."""
    if isinstance(expected, dict):
        if not isinstance(observed, dict) or set(expected) != set(observed):
            raise ValueError(f"recomputed {location} fields differ")
        for key, value in expected.items():
            _assert_record_matches(value, observed[key], f"{location}.{key}")
    elif isinstance(expected, list):
        if not isinstance(observed, list) or len(expected) != len(observed):
            raise ValueError(f"recomputed {location} sequence differs")
        for index, value in enumerate(expected):
            _assert_record_matches(value, observed[index], f"{location}[{index}]")
    elif isinstance(expected, float):
        if (
            not isinstance(observed, (float, int))
            or isinstance(observed, bool)
            or not math.isfinite(observed)
            or not math.isclose(expected, observed, abs_tol=1e-9, rel_tol=1e-12)
        ):
            raise ValueError(f"recomputed {location} value differs")
    elif type(expected) is not type(observed) or expected != observed:
        raise ValueError(f"recomputed {location} value differs")


def _validate_saved_run(
    run: dict[str, Any],
    reference: Any,
    manifest: dict[str, Any],
    environment: dict[str, Any],
    output: Path,
) -> None:
    """Validate saved identities and evidence before reuse or acceptance.

    Resume never removes a failed attempt or replaces its seed. A malformed,
    failed or mismatched record causes an explicit refusal.
    """
    if run.get("status") != "COMPLETE":
        raise ValueError("saved run is not complete; its failed attempt must be retained")
    p, seed = run.get("p"), run.get("initialization_seed")
    if (
        type(p) is not int
        or type(seed) is not int
        or p not in PRIMARY_CONFIG["p_values"]
        or seed not in PRIMARY_CONFIG["restart_seeds"]
    ):
        raise ValueError("saved run uses a seed/depth outside the primary configuration")
    config = _expected_config(p, seed)
    if (
        run.get("instance_id") != reference.instance.instance_id
        or run.get("m2_reference_hash") != reference.reference_hash
        or run.get("qubo_hash") != reference.qubo_hash
        or run.get("ising_hash") != reference.ising_hash
        or run.get("qubit_count") != reference.qubo.number_variables
        or run.get("config") != config
        or run.get("manifest_content_sha256") != manifest["manifest_content_sha256"]
    ):
        raise ValueError("saved run reference, manifest or configuration differs")
    previous_environment = run.get("environment", {})
    for key in ("git_commit", "dependency_lock_sha256", "python", "qiskit", "scipy", "numpy"):
        if previous_environment.get(key) != environment.get(key):
            raise ValueError(f"saved run environment differs: {key}")
    if not run.get("operator_equivalence") or not run.get("expectation_verified"):
        raise ValueError("saved run has no successful operator/expectation verification")
    if type(run.get("ground_state_count")) is not int or run.get("ground_state_count") != len(
        reference.ground_bits
    ):
        raise ValueError("saved run ground-state count differs from frozen reference")
    import numpy as np
    from qiskit.quantum_info import Statevector

    from km_quantum.qaoa.analysis import analysis_for_probabilities, build_state_table
    from km_quantum.qaoa.sampling import analyze_counts
    from km_quantum.qaoa.simulation import bind_parameters, build_qaoa

    if reference.reference_hash not in _VALIDATION_TABLE_CACHE:
        _VALIDATION_TABLE_CACHE[reference.reference_hash] = build_state_table(
            reference, max_qubits=PRIMARY_CONFIG["max_qubits"]
        )
    table = _VALIDATION_TABLE_CACHE[reference.reference_hash]
    prepared = build_qaoa(reference.ising, p, max_qubits=PRIMARY_CONFIG["max_qubits"])
    actual_order = [parameter.name for parameter in prepared.parameters]
    if run.get("parameter_order") != actual_order or run.get("parameter_count") != len(
        prepared.parameters
    ):
        raise ValueError("saved run parameter identities differ from the actual Qiskit circuit")
    expected_bindings = [
        {
            "position": index,
            "name": name,
            "initial_value": run["initial_parameters"][index],
            "final_value": run["final_parameters"][index],
        }
        for index, name in enumerate(actual_order)
    ]
    _assert_record_matches(expected_bindings, run.get("parameter_bindings"), "parameter bindings")
    initial = np.random.default_rng(seed).uniform(0.0, np.pi, len(prepared.parameters))
    if run.get("initial_parameters") != initial.tolist():
        raise ValueError("saved initial parameters differ from the prespecified seeded values")
    bound = bind_parameters(prepared, run["final_parameters"])
    probabilities = np.asarray(Statevector.from_instruction(bound).probabilities(), dtype=float)
    recomputed = analysis_for_probabilities(
        reference, table, probabilities, run["final_expected_energy"]
    )
    for key, value in recomputed.items():
        _assert_record_matches(value, run.get(key), f"final analysis.{key}")
    initial_probabilities = np.asarray(
        Statevector.from_instruction(bind_parameters(prepared, initial)).probabilities(),
        dtype=float,
    )
    initial_energy = float(np.dot(initial_probabilities, table.energies))
    _assert_record_matches(initial_energy, run.get("initial_expected_energy"), "initial energy")
    tolerance = PRIMARY_CONFIG["numerical_tolerances"]
    if not math.isclose(
        run["final_expected_energy"],
        run["expected_energy"],
        abs_tol=tolerance["atol"],
        rel_tol=tolerance["rtol"],
    ):
        raise ValueError("saved run estimator/distribution expectations differ")
    if not math.isclose(
        run["ground_state_probability"], run["optimal_valid_probability"], abs_tol=1e-12
    ):
        raise ValueError("saved run ground/energy-consistent optimum probabilities differ")
    for key in ("ground_state_probability", "valid_probability", "invalid_probability"):
        value = run[key]
        if (
            not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not -1e-12 <= value <= 1 + 1e-12
        ):
            raise ValueError(f"saved run probability is invalid: {key}")
    if not math.isclose(run["valid_probability"] + run["invalid_probability"], 1, abs_tol=1e-12):
        raise ValueError("saved run validity probabilities do not partition the distribution")
    run_id = f"{reference.name}-p{p}-seed{seed}"
    trace = output / "traces" / f"{run_id}.jsonl"
    sample_path = output / "samples" / f"{run_id}.json"
    if not trace.is_file() or _file_hash(trace) != run.get("trace_sha256"):
        raise ValueError("saved optimization trace is missing or changed")
    if not sample_path.is_file() or _file_hash(sample_path) != run.get("sample_sha256"):
        raise ValueError("saved sampling record is missing or changed")
    evaluations = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    if (
        len(evaluations) != run.get("nfev", -1) + 1
        or len(evaluations) != run.get("total_expectation_evaluations")
        or run.get("verification_evaluations") != 1
        or [row.get("evaluation") for row in evaluations] != list(range(1, len(evaluations) + 1))
        or evaluations[-1].get("phase") != "verification"
    ):
        raise ValueError(
            "saved trace does not contain every optimizer call plus final verification"
        )
    parameter_count = run["parameter_count"]
    if (
        len(run.get("initial_parameters", [])) != parameter_count
        or len(run.get("final_parameters", [])) != parameter_count
    ):
        raise ValueError("saved actual parameter count differs from angle records")
    for row in evaluations:
        if len(row.get("parameters", [])) != parameter_count or not all(
            isinstance(value, (int, float)) and math.isfinite(value) for value in row["parameters"]
        ):
            raise ValueError("saved trace has malformed parameters")
    if (
        evaluations[0]["parameters"] != run["initial_parameters"]
        or evaluations[-1]["parameters"] != run["final_parameters"]
    ):
        raise ValueError("saved trace initial/final angles differ from the run")
    if not math.isclose(
        evaluations[-1]["expected_energy"], run["final_expected_energy"], abs_tol=1e-12
    ):
        raise ValueError("saved final trace expectation differs from the run")
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    if sample != run.get("sampling"):
        raise ValueError("embedded and standalone sampling records differ")
    counts = sample.get("counts", {})
    if (
        sample.get("shots") != config["shots"]
        or sample.get("sampler_seed") != config["sampler_seed"]
        or not sample.get("sampling_verified")
        or sample.get("sampling_tail_alpha") != 1e-9
        or not counts
        or any(type(count) is not int or count < 1 for count in counts.values())
        or sum(counts.values()) != config["shots"]
        or sample.get("ground_state_samples", -1) < 0
        or sample.get("valid_sample_count", -1) + sample.get("invalid_sample_count", -1)
        != config["shots"]
        or sample.get("exact_ground_probability") != run["ground_state_probability"]
    ):
        raise ValueError("saved seeded sampling evidence is malformed or unverified")
    recomputed_sample = analyze_counts(
        reference,
        table,
        counts,
        shots=config["shots"],
        seed=config["sampler_seed"],
        exact_ground_probability=recomputed["ground_state_probability"],
    )
    _assert_record_matches(recomputed_sample, sample, "sampling")


def _load_completed_runs(
    references: dict[str, Any],
    manifest: dict[str, Any],
    environment: dict[str, Any],
    output: Path,
) -> list[dict[str, Any]]:
    """Load validated complete study records across both execution splits.

    ERROR records remain in the output and acceptance audit. A complete record
    with missing or inconsistent evidence is refused rather than omitted from
    summaries. The manifest bounds collection, excluding unrelated run files.
    """
    runs = []
    for entry in manifest["primary_suite"]:
        reference = references[entry["reference_id"]]
        for p in PRIMARY_CONFIG["p_values"]:
            for seed in PRIMARY_CONFIG["restart_seeds"]:
                run_id = f"{reference.name}-p{p}-seed{seed}"
                path = output / "runs" / f"{run_id}.json"
                if not path.is_file():
                    continue
                run = json.loads(path.read_text(encoding="utf-8"))
                if run.get("status") != "COMPLETE":
                    continue
                if run.get("p") != p or run.get("initialization_seed") != seed:
                    raise ValueError(f"saved file identity differs from its run: {run_id}")
                _validate_saved_run(run, reference, manifest, environment, output)
                runs.append(run)
    return runs


def run_benchmark(
    root: str | Path,
    output_dir: str | Path,
    split: str = "all",
    resume: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Execute the frozen primary study, retaining every configured restart.

    All 113 correctness gates complete before any primary QAOA run. Development
    runs precede evaluation runs. Splits only filter execution; they never change
    selection, seeds, depths or budgets. Partial studies cannot receive PASS.
    """
    from km_quantum.qaoa.models import QAOAConfig
    from km_quantum.qaoa.sampling import sample_state
    from km_quantum.qaoa.simulation import run_qaoa

    if split not in ("all", "development", "evaluation"):
        raise ValueError("split must be all, development or evaluation")
    root, output = Path(root).resolve(), Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = create_manifest(root)
    try:
        gate, references, tables = _full_gate(root, output, manifest, progress)
    except Exception as error:
        failure = {"passed": False, "error_type": type(error).__name__, "error": str(error)}
        _write_json(output / "gate_failure.json", failure)
        raise
    environment = gate["environment"]
    if environment.get("git_dirty") is not False or not environment.get("git_commit"):
        raise ValueError("primary benchmarking requires a clean committed source checkout")
    entries = sorted(
        manifest["primary_suite"],
        key=lambda entry: (entry["split"] != "development", entry["reference_id"]),
    )
    selected = [entry for entry in entries if split == "all" or entry["split"] == split]
    planned = len(selected) * len(PRIMARY_CONFIG["p_values"]) * len(PRIMARY_CONFIG["restart_seeds"])
    completed = resumed = 0
    failed = []
    runs = []
    for entry in selected:
        reference = references[entry["reference_id"]]
        table = tables[reference.name]
        for p in PRIMARY_CONFIG["p_values"]:
            for seed in PRIMARY_CONFIG["restart_seeds"]:
                config_data = _expected_config(p, seed)
                config = QAOAConfig.model_validate(config_data)
                run_id = f"{reference.name}-p{p}-seed{seed}"
                run_path = output / "runs" / f"{run_id}.json"
                trace_path = output / "traces" / f"{run_id}.jsonl"
                sample_path = output / "samples" / f"{run_id}.json"
                if run_path.exists():
                    if not resume:
                        raise ValueError(
                            f"run already exists; explicit resume required: {run_path}"
                        )
                    saved = json.loads(run_path.read_text(encoding="utf-8"))
                    _validate_saved_run(saved, reference, manifest, environment, output)
                    runs.append(saved)
                    completed += 1
                    resumed += 1
                    continue
                if trace_path.exists() or sample_path.exists():
                    raise ValueError(
                        f"partial attempt artifacts exist and must be retained: {run_id}"
                    )
                try:
                    run, bound, _ = run_qaoa(reference, config, trace_path=trace_path, table=table)
                    sample = sample_state(
                        reference,
                        bound,
                        table,
                        shots=config.shots,
                        seed=config.sampler_seed,
                        exact_ground_probability=run["ground_state_probability"],
                    )
                    _write_json(sample_path, sample)
                    run.update(
                        {
                            "split": entry["split"],
                            "manifest_content_sha256": manifest["manifest_content_sha256"],
                            "sampling": sample,
                            "sample_path": str(sample_path),
                            "sample_sha256": _file_hash(sample_path),
                            "trace_sha256": _file_hash(trace_path),
                        }
                    )
                    _write_json(run_path, run)
                    _validate_saved_run(run, reference, manifest, environment, output)
                    runs.append(run)
                    completed += 1
                except Exception as error:
                    failure = {
                        "status": "ERROR",
                        "run_id": run_id,
                        "instance_id": reference.instance.instance_id,
                        "reference_name": reference.name,
                        "p": p,
                        "initialization_seed": seed,
                        "config": config_data,
                        "environment": environment,
                        "manifest_content_sha256": manifest["manifest_content_sha256"],
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                    # Preserve any computed COMPLETE record as well as the failure.
                    failure_path = output / "failures" / f"{run_id}.json"
                    _write_json(failure_path, failure)
                    if not run_path.exists():
                        _write_json(run_path, failure)
                    failed.append(failure)
                if progress is not None:
                    progress(f"M3 {entry['split']}: {completed}/{planned} complete; {run_id}")
    # A development/evaluation invocation only filters new execution. Shared
    # summaries and plots always describe all saved completed study records.
    runs = _load_completed_runs(references, manifest, environment, output)
    summaries = aggregate_runs(runs)
    _write_json(output / "summaries/restarts.json", {"summaries": summaries})
    _write_json(
        output / "summaries/exploratory_correlations.json",
        exploratory_correlations(summaries, manifest),
    )
    plots = (
        create_plots(
            runs, [output / "traces" / f"{run['run_id']}.jsonl" for run in runs], output / "plots"
        )
        if runs
        else []
    )
    report = {
        "status": "COMPLETE" if completed == planned and not failed else "INCOMPLETE",
        "split": split,
        "planned_runs": planned,
        "completed_runs": completed,
        "resumed_runs": resumed,
        "failed_runs": len(failed),
        "failures": failed,
        "summary_run_count": len(runs),
        "summary_scope": "all saved validated COMPLETE primary-manifest runs across both splits",
        "plots": plots,
        "manifest_content_sha256": manifest["manifest_content_sha256"],
    }
    _write_json(output / "benchmark_execution.json", report)
    build_acceptance(root, output)
    return report


def build_acceptance(
    root: str | Path,
    output_dir: str | Path,
    tooling: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit all 660 planned identities and saved evidence; quality never gates PASS."""
    from km_quantum.qaoa.references import load_references

    root, output = Path(root).resolve(), Path(output_dir).resolve()
    manifest = create_manifest(root)
    references = load_references(root)
    provenance = verify_provenance(root)
    gate_path = output / "operator_gate.json"
    gate = json.loads(gate_path.read_text(encoding="utf-8")) if gate_path.exists() else {}
    expected_gate_names = set(manifest["correctness_reference_ids"])
    gate_records = gate.get("references", [])
    actual_gate_names = [record.get("reference_id") for record in gate_records]
    gate_valid = (
        gate.get("passed") is True
        and gate.get("manifest_content_sha256") == manifest["manifest_content_sha256"]
        and len(actual_gate_names) == len(expected_gate_names)
        and set(actual_gate_names) == expected_gate_names
        and gate.get("reference_count") == len(expected_gate_names)
        and gate.get("checked_states")
        == sum(1 << references[name].qubo.number_variables for name in expected_gate_names)
        and gate.get("mismatch_count") == 0
        and all(
            record.get("passed") is True
            and record.get("state_table_checked_ground_set") is True
            and record.get("reference_sha256") == references[record["reference_id"]].reference_hash
            and record.get("checked_states")
            == 1 << references[record["reference_id"]].qubo.number_variables
            for record in gate_records
        )
    )
    try:
        m2_acceptance = _verify_m2_acceptance(root, references)
    except (OSError, ValueError, KeyError) as error:
        m2_acceptance = {"passed": False, "error": str(error)}
    expected = {
        (entry["reference_id"], p, seed)
        for entry in manifest["primary_suite"]
        for p in PRIMARY_CONFIG["p_values"]
        for seed in PRIMARY_CONFIG["restart_seeds"]
    }
    accepted: list[dict[str, Any]] = []
    missing = []
    invalid = []
    for name, p, seed in sorted(expected):
        run_id = f"{name}-p{p}-seed{seed}"
        path = output / "runs" / f"{run_id}.json"
        if not path.exists():
            missing.append(run_id)
            continue
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
            if run.get("p") != p or run.get("initialization_seed") != seed:
                raise ValueError("file identity differs from saved run seed/depth")
            _validate_saved_run(
                run, references[name], manifest, gate.get("environment", {}), output
            )
            accepted.append(run)
        except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
            invalid.append({"run_id": run_id, "error": str(error)})
    expected_files = {f"{name}-p{p}-seed{seed}.json" for name, p, seed in expected}
    unexpected = sorted(
        path.name for path in (output / "runs").glob("*.json") if path.name not in expected_files
    )
    failure_files = sorted(path.name for path in (output / "failures").glob("*.json"))
    checks = tooling or {}

    def passed(name: str) -> bool:
        value = checks.get(name)
        if isinstance(value, dict):
            return value.get("status") == "PASS" and value.get("failed", 0) == 0
        return value is True

    tooling_passed = all(passed(name) for name in ("pytest", "ruff", "format", "mypy", "build"))
    complete = (
        provenance["unchanged"]
        and m2_acceptance["passed"]
        and gate_valid
        and len(accepted) == len(expected)
        and not missing
        and not invalid
        and not unexpected
        and not failure_files
        and tooling_passed
    )
    report = {
        "milestone": "M3",
        "status": "PASS" if complete else "INCOMPLETE",
        "m2_reference_commit": manifest["m2_reference_commit"],
        "manifest_content_sha256": manifest["manifest_content_sha256"],
        "m2_provenance": provenance,
        "m2_acceptance": m2_acceptance,
        "operator_equivalence": gate_valid,
        "bit_order_verified": gate_valid,
        "operator_reference_count": gate.get("reference_count", 0),
        "operator_checked_states": gate.get("checked_states", 0),
        "operator_max_abs_difference": gate.get("max_abs_difference"),
        "planned_runs": len(expected),
        "verified_runs": len(accepted),
        "missing_runs": missing,
        "invalid_runs": invalid,
        "unexpected_run_files": unexpected,
        "preserved_failure_files": failure_files,
        "p_values": PRIMARY_CONFIG["p_values"],
        "restart_seeds": PRIMARY_CONFIG["restart_seeds"],
        "statevector_expectation_verified": len(accepted) == len(expected),
        "sampling_verified": len(accepted) == len(expected),
        "complete_traces_verified": len(accepted) == len(expected),
        "optimizer_converged_runs": sum(bool(run["success"]) for run in accepted),
        "performance_is_acceptance_criterion": False,
        "hardware_used": False,
        "noise_used": False,
        "warm_start_used": False,
        "tooling": checks,
        "tooling_passed": tooling_passed,
    }
    _write_json(output / "m3-acceptance.json", report)
    return report
