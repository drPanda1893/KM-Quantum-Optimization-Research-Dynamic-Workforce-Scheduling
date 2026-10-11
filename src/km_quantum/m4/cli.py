"""Additive M4 commands with explicit partial-study and resource status."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any


def add_m4_parser[ParserT: argparse.ArgumentParser](
    commands: argparse._SubParsersAction[ParserT],
) -> None:
    parser = commands.add_parser("m4", help="Controlled local noise transfer and warm-start study")
    actions = parser.add_subparsers(dest="m4_command", required=True, parser_class=type(parser))
    manifest = actions.add_parser("manifest", help="Freeze the bounded pre-evaluation protocol")
    verify = actions.add_parser("verify", help="Run reference, mixer, compiler and noise gates")
    benchmark = actions.add_parser(
        "benchmark", help="Ideal training then frozen-parameter noise transfer"
    )
    benchmark.add_argument(
        "--dry-run", action="store_true", help="Show the fixed run/resource plan"
    )
    benchmark.add_argument(
        "--resume", action="store_true", help="Reuse only fully audited completed records"
    )
    benchmark.add_argument(
        "--smoke", action="store_true", help="Known A/p1/seed0 technical smoke only"
    )
    summarize = actions.add_parser(
        "summarize", help="Rebuild complete or explicitly partial summaries/plots"
    )
    acceptance = actions.add_parser(
        "acceptance", help="Audit implementation, complete experiment and tooling"
    )
    acceptance.add_argument("--tooling", help="Recorded tests/lint/format/typecheck/build JSON")
    for action in (manifest, verify, benchmark, summarize, acceptance):
        action.add_argument("--root", default=".")
        action.add_argument("--output-directory", default="results/m4")


def execute_m4(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    from km_quantum.m4.benchmark import load_records, output_directory, run_benchmark
    from km_quantum.m4.io import read_json, write_json
    from km_quantum.m4.manifest import freeze_manifest, load_config, validate_manifest
    from km_quantum.m4.reporting import create_plots, summarize_records
    from km_quantum.m4.verification import verify_m4

    root = Path(args.root).resolve()
    output = output_directory(root, args.output_directory)
    command = args.m4_command
    if command == "manifest":
        manifest = freeze_manifest(root)
        return {
            "status": "FROZEN",
            "manifest_content_sha256": manifest["manifest_content_sha256"],
            "config_hash": manifest["config_hash"],
            "counts": manifest["counts"],
        }, 0
    config = load_config(root)
    manifest = validate_manifest(root, config)
    if command == "verify":
        result = verify_m4(root, output, config, manifest)
        return result, 0 if result["status"] == "PASS" else 1
    if command == "benchmark":
        result = run_benchmark(
            root, output, resume=args.resume, dry_run=args.dry_run, smoke=args.smoke
        )
        return result, 0 if result["status"] in ("COMPLETE", "DRY_RUN") else 1
    if command == "summarize":
        records, controls = load_records(output)
        result = summarize_records(records, controls, manifest)
        write_json(output / "summary.json", result)
        plots = create_plots(result, output)
        return {
            "status": result["status"],
            "summary": str(output / "summary.json"),
            "plots": [str(path) for path in plots],
        }, 0
    from km_quantum.m4.acceptance import build_acceptance

    tooling = read_json(args.tooling) if args.tooling else None
    result = build_acceptance(root, output, manifest, config, tooling)
    return result, 0 if result["status"] == "PASS" else 1
