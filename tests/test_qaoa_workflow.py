"""Public ideal-QAOA commands preserve traces and reject inconsistent saved inputs."""

import json
from pathlib import Path

from km_quantum.__main__ import main

ROOT = Path(__file__).resolve().parents[1]


def test_inspect_exposes_frozen_offset_and_actual_parameter_names(capsys) -> None:
    assert (
        main(["qaoa", "inspect", "--root", str(ROOT), "--reference", "A_single_job", "--p", "2"])
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["ground_bits_m2_order"] == ["10"]
    assert result["circuit"]["parameter_count"] == 4
    assert len(result["circuit"]["parameter_order"]) == 4
    assert not result["circuit"]["hardware_transpilation"]


def test_public_operator_command_checks_every_state(tmp_path, capsys) -> None:
    output = tmp_path / "operator.json"
    assert (
        main(
            [
                "qaoa",
                "verify-operator",
                "--root",
                str(ROOT),
                "--reference",
                "I_multiple_optima",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    report = json.loads(output.read_text())
    assert report["checked_states"] == 8
    assert report["mismatch_count"] == 0
    assert report["status"] == "PASS"


def test_public_run_and_resampling_persist_complete_inputs(tmp_path, capsys) -> None:
    output = tmp_path / "run.json"
    assert (
        main(
            [
                "qaoa",
                "run",
                "--root",
                str(ROOT),
                "--reference",
                "A_single_job",
                "--maxiter",
                "10",
                "--shots",
                "128",
                "--seed",
                "4",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    result = json.loads(output.read_text())
    trace = [
        json.loads(line) for line in output.with_suffix(".trace.jsonl").read_text().splitlines()
    ]
    assert len(trace) == result["total_expectation_evaluations"] == result["nfev"] + 1
    assert trace[-1]["phase"] == "verification"
    assert result["sampling"]["shots"] == 128
    assert result["expectation_verified"]
    assert result["environment"]["qiskit"] == "2.5.2"
    sample = tmp_path / "sample.json"
    assert (
        main(
            [
                "qaoa",
                "sample",
                "--root",
                str(ROOT),
                "--run",
                str(output),
                "--shots",
                "256",
                "--seed",
                "19",
                "--output",
                str(sample),
            ]
        )
        == 0
    )
    sampled = json.loads(sample.read_text())
    assert sum(sampled["counts"].values()) == 256
    assert sampled["sampling_verified"]
    capsys.readouterr()
    assert (
        main(
            [
                "qaoa",
                "run",
                "--root",
                str(ROOT),
                "--reference",
                "A_single_job",
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert "already exists" in capsys.readouterr().err


def test_unknown_reference_and_malformed_saved_run_are_structured_errors(tmp_path, capsys) -> None:
    assert main(["qaoa", "inspect", "--root", str(ROOT), "--reference", "does-not-exist"]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "input_error"
    saved = tmp_path / "bad.json"
    saved.write_text("{}")
    assert (
        main(
            [
                "qaoa",
                "sample",
                "--root",
                str(ROOT),
                "--run",
                str(saved),
                "--output",
                str(tmp_path / "out.json"),
            ]
        )
        == 2
    )
    assert "missing required" in capsys.readouterr().err


def test_saved_parameter_order_is_checked_before_resampling(tmp_path, capsys) -> None:
    from km_quantum.qaoa.references import load_references

    reference = load_references(ROOT)["A_single_job"]
    saved = tmp_path / "wrong-order.json"
    saved.write_text(
        json.dumps(
            {
                "reference_name": reference.name,
                "m2_reference_hash": reference.reference_hash,
                "p": 1,
                "parameter_order": ["wrong", "order"],
                "final_parameters": [0, 0],
                "ground_state_probability": 0.25,
            }
        )
    )
    assert (
        main(
            [
                "qaoa",
                "sample",
                "--root",
                str(ROOT),
                "--run",
                str(saved),
                "--output",
                str(tmp_path / "out.json"),
            ]
        )
        == 2
    )
    assert "ordering differs" in capsys.readouterr().err
