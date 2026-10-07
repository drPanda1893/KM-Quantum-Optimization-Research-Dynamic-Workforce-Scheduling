"""Distribution boundaries, reproducibility and hand-case artifact consistency."""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from km_quantum.domain import SchedulingInstance
from km_quantum.instances import GeneratorConfig, generate_instance, hand_cases

ROOT = Path(__file__).resolve().parents[1]


def test_identical_config_is_reproducible() -> None:
    config = GeneratorConfig(seed=928)
    first = generate_instance(config)
    second = generate_instance(GeneratorConfig.model_validate_json(config.model_dump_json()))
    assert first.instance_id == second.instance_id
    assert first.model_dump_json() == second.model_dump_json()
    assert first.generator_parameters == config.model_dump(mode="json")
    assert "timestamp" not in first.model_dump_json()


def test_explicit_defaults_and_implicit_defaults_are_identical() -> None:
    defaults = GeneratorConfig()
    explicit = GeneratorConfig.model_validate(defaults.model_dump())
    assert (
        generate_instance(defaults).model_dump_json()
        == generate_instance(explicit).model_dump_json()
    )


def test_config_and_seed_are_part_of_identity() -> None:
    default = generate_instance(GeneratorConfig(seed=1))
    another_seed = generate_instance(GeneratorConfig(seed=2))
    another_density = generate_instance(GeneratorConfig(seed=1, compatibility_density=0.1))
    assert len({default.instance_id, another_seed.instance_id, another_density.instance_id}) == 3


def test_does_not_consume_global_random_state() -> None:
    previous = random.getstate()
    generate_instance(GeneratorConfig(seed=19))
    assert random.getstate() == previous


def test_reproducible_across_hash_seeds() -> None:
    program = (
        "from km_quantum.instances import GeneratorConfig, generate_instance; "
        "print(generate_instance(GeneratorConfig(seed=31, number_of_skills=7)).model_dump_json())"
    )
    outputs = [
        subprocess.check_output(
            [sys.executable, "-c", program],
            cwd=ROOT,
            env={**os.environ, "PYTHONHASHSEED": str(hash_seed)},
            text=True,
        )
        for hash_seed in (1, 271)
    ]
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize("density", [0.0, 1.0])
def test_skill_compatibility_endpoints(density: float) -> None:
    instance = generate_instance(GeneratorConfig(compatibility_density=density))
    assert instance.metadata["observed_skill_compatibility_density"] == density
    assert all(len(worker.skills) == (2 if density else 0) for worker in instance.workers)


@pytest.mark.parametrize("density", [0.0, 1.0])
def test_temporal_construction_endpoints(density: float) -> None:
    instance = generate_instance(GeneratorConfig(overlap_density=density))
    assert instance.metadata["observed_pairwise_overlap_density"] == density
    if density:
        assert all(job.start_minute == 0 for job in instance.jobs)
        assert instance.metadata["shared_start_constructions"] == len(instance.jobs) - 1
    else:
        assert all(
            earlier.end_minute <= later.start_minute
            for earlier, later in zip(instance.jobs, instance.jobs[1:], strict=False)
        )


@pytest.mark.parametrize("tightness, capacity", [(0.0, 120), (0.5, 120), (1.0, 60), (2.0, 30)])
def test_workload_ratio_and_rounding(tightness: float, capacity: int) -> None:
    instance = generate_instance(
        GeneratorConfig(
            number_of_workers=2,
            number_of_jobs=4,
            min_duration_minutes=30,
            max_duration_minutes=30,
            workload_tightness=tightness,
        )
    )
    assert all(worker.max_work_minutes == capacity for worker in instance.workers)
    assert instance.metadata["total_job_minutes"] == 120


def test_tiny_positive_workload_ratio_does_not_overflow() -> None:
    instance = generate_instance(GeneratorConfig(workload_tightness=1e-310))
    assert all(worker.max_work_minutes == 240 for worker in instance.workers)


def test_priority_weights_and_required_coverage() -> None:
    instance = generate_instance(
        GeneratorConfig(priority_distribution=(0, 0, 7), must_assign_fraction=1)
    )
    assert all(job.priority == 3 and job.must_assign for job in instance.jobs)


def test_duration_horizon_skill_and_optional_cost_bounds() -> None:
    instance = generate_instance(
        GeneratorConfig(
            seed=21,
            min_duration_minutes=15,
            max_duration_minutes=20,
            horizon_minutes=120,
            min_cost_per_hour=1500,
            max_cost_per_hour=2500,
        )
    )
    for job in instance.jobs:
        assert 15 <= job.duration_minutes <= 20
        assert 0 <= job.start_minute < job.end_minute <= 120
        assert len(job.required_skills) == 1
    for worker in instance.workers:
        assert worker.cost_per_hour is not None
        assert 1500 <= worker.cost_per_hour <= 2500
        assert worker.availability[0].start_minute == 0
        assert worker.availability[0].end_minute == 120


@pytest.mark.parametrize("workers, jobs", [(0, 0), (0, 3), (3, 0)])
def test_empty_domain_dimensions(workers: int, jobs: int) -> None:
    instance = generate_instance(GeneratorConfig(number_of_workers=workers, number_of_jobs=jobs))
    assert len(instance.workers) == workers
    assert len(instance.jobs) == jobs
    assert instance.metadata["observed_skill_compatibility_density"] == 0
    assert SchedulingInstance.model_validate_json(instance.model_dump_json()) == instance


@pytest.mark.parametrize(
    "values",
    [
        {"number_of_workers": -1},
        {"number_of_jobs": -1},
        {"number_of_skills": 0},
        {"number_of_jobs": True},
        {"seed": 3.0},
        {"seed": -1},
        {"compatibility_density": -0.1},
        {"compatibility_density": 1.1},
        {"overlap_density": 1.1},
        {"overlap_density": float("nan")},
        {"workload_tightness": -0.1},
        {"workload_tightness": float("inf")},
        {"priority_distribution": ()},
        {"priority_distribution": (0, 0)},
        {"priority_distribution": (1, -1)},
        {"priority_distribution": (float("nan"),)},
        {"priority_distribution": (1e308, 1e308)},
        {"min_duration_minutes": 0},
        {"min_duration_minutes": 61},
        {"horizon_minutes": 239},
        {"must_assign_fraction": 1.1},
        {"min_cost_per_hour": 1},
        {"max_cost_per_hour": 1},
        {"min_cost_per_hour": 3, "max_cost_per_hour": 2},
        {"unknown_parameter": 1},
    ],
)
def test_invalid_config_is_rejected(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        GeneratorConfig(**values)


def test_config_is_frozen() -> None:
    config = GeneratorConfig()
    with pytest.raises(ValidationError):
        config.seed = 1


def test_all_hand_cases_match_committed_json() -> None:
    cases = hand_cases()
    assert len(cases) == 10
    fixture_directory = ROOT / "data" / "synthetic" / "hand_cases"
    assert {path.stem for path in fixture_directory.glob("*.json")} == set(cases)
    for name, instance in cases.items():
        raw = (fixture_directory / f"{name}.json").read_text(encoding="utf-8")
        assert SchedulingInstance.model_validate_json(raw) == instance
        assert raw == instance.model_dump_json(indent=2) + "\n"
        assert instance.metadata["source"] == "hand-verifiable"


@pytest.mark.parametrize("family", ["easy", "tight"])
def test_generated_families_match_committed_configs(family: str) -> None:
    config = GeneratorConfig.model_validate_json(
        (ROOT / "configs" / f"generator_{family}.json").read_text(encoding="utf-8")
    )
    generated = generate_instance(config)
    raw = (ROOT / "data" / "synthetic" / f"{family}.json").read_text(encoding="utf-8")
    assert raw == generated.model_dump_json(indent=2) + "\n"
    assert generated.generator_parameters == json.loads(config.model_dump_json())
