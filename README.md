# KM³ Quantum Optimization Research — Dynamic Workforce Scheduling

Research project exploring whether **quantum and hybrid quantum-classical optimization** can add measurable value to real-world dynamic workforce scheduling.

## Core idea

A workforce scheduler must assign jobs to workers while respecting skills, availability, time conflicts and operational constraints. The project compares:

1. **Classical optimization** — Google OR-Tools CP-SAT
2. **Quantum optimization** — QAOA on simulators and selected real QPUs
3. **Hybrid optimization** — classical preprocessing / warm-start / decomposition combined with QAOA

The project does **not** assume quantum advantage. The goal is to measure when quantum or hybrid methods help, when they do not, and what is practically useful for KM³.

## First real-world target

Field-service / operations scheduling:

- workers with skills and availability
- jobs with requirements, priorities and time windows
- conflicts and capacity limits
- dynamic events such as sickness, delays or urgent jobs
- minimal disruption when a schedule must be repaired

## Research principle

> Strong classical baselines first. Quantum claims only after reproducible evidence.

A negative result is a valid research result. A useful KM³ optimization product must remain valuable even if the quantum track shows no advantage.

## Status

**M1 — Classical Scheduling Baseline**

Implemented: validated fixed-slot scheduling models, deterministic synthetic
instances, independent feasibility validator and objective evaluator, OR-Tools
CP-SAT, a tiny exhaustive reference solver, ten hand-verifiable cases, and a
JSON/JSONL experiment CLI. Every returned incumbent is independently checked.
Only proven optima serve as objective ground truth.

M1 contains no QUBO, QAOA, IBM integration, customer data, or product UI.
The research roadmap remains in [RESEARCH_PLAN.md](RESEARCH_PLAN.md).

## Run locally

Install [uv](https://docs.astral.sh/uv/), then use the locked Python 3.12 environment:

```sh
uv sync --locked --all-groups
uv run --locked python -m pytest
uv run --locked ruff check .
uv run --locked mypy
```

Runtime dependencies are OR-Tools and Pydantic; pytest, Ruff, and mypy are
development tools. Exact resolved versions are committed in `uv.lock`.

## Reproducible workflow

```sh
uv run --locked python -m km_quantum generate --config configs/generator_easy.json --seed 42 --output data/synthetic/run-42.json
uv run --locked python -m km_quantum solve --instance data/synthetic/run-42.json --config configs/baseline.json --output results/run-42.json
uv run --locked python -m km_quantum validate --instance data/synthetic/run-42.json --schedule results/run-42.json
uv run --locked python -m km_quantum benchmark --instance data/synthetic/run-42.json --config configs/baseline.json --experiment-id m1-example --output results/m1-example.jsonl
```

Use `--solver exhaustive` on tiny instances for a second reference. The CLI
also exposes generator/search parameter overrides; inspect each command's
`--help`. It prints JSON and returns 0 for success/feasibility, 1 for absent or
invalid schedules, and 2 for usage/input/IO errors. Benchmark files append one
record per run, including input/configuration, raw output, independent metrics,
versions, dependency-lock hash, timestamp, and Git state.

The default objective is priority-weighted unassigned penalties. Optional worker
cost and workload-range weights default to zero. This is a weighted sum, not
lexicographic coverage. All job intervals are fixed and half-open; travel and
dynamic repair are outside M1. Time limits can prevent proof of optimality.

See [the mathematical model](docs/model.md) and
[the experiment protocol](docs/experiment_protocol.md) for exact units,
constraints, objective arithmetic, generator semantics, reproducibility, and
fair comparison rules.

## Repository layout

```text
src/km_quantum/
  domain/               Pydantic input, schedule and solver schemas
  instances/            Versioned synthetic generator and hand cases
  solvers/classical/    CP-SAT and tiny exhaustive reference
  metrics/              Independent feasibility and objective checks
  experiments/          JSON helpers and benchmark records
tests/                  Mathematical, solver, reproducibility and workflow tests
configs/                Baseline and easy/tight generator configurations
data/synthetic/         Synthetic families and ten hand-verifiable fixtures
docs/                   Model, experiment protocol and implementation decision
```

The next milestone is **M2: QUBO mapping + equivalence verification** against
the original constrained model. M2 implementation has not begun.

## Intended outputs

- formal scheduling model
- reproducible synthetic benchmark suite
- strong classical baseline
- QUBO / Ising mapping with equivalence tests
- standard QAOA and warm-start QAOA
- noisy-simulator and real-QPU experiments
- hybrid decomposition / schedule-repair experiments
- scientific report with raw results and limitations
- product decision for a future KM³ Optimization Engine

## License / data

No customer or employee personal data should be committed to this repository. Initial experiments use synthetic instances only. Any later pilot dataset must be anonymized/pseudonymized and handled outside the public repository.
