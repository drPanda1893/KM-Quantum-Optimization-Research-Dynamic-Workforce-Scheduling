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

**M3 — Ideal QAOA**

Implemented: validated fixed-slot scheduling models, deterministic synthetic
instances, independent feasibility validator and objective evaluator, OR-Tools
CP-SAT, a tiny exhaustive reference solver, ten hand-verifiable cases, and a
JSON/JSONL experiment CLI. Every returned incumbent is independently checked.
Only proven optima serve as objective ground truth.

M1 remains the frozen classical reference. M2 adds a canonical pruned binary
mapping, exact integer QUBO/Ising representations, bounded slack and imbalance
auxiliaries, derived safe penalties, all-minima exhaustive analysis, and an
automated equivalence gate against both M1 oracles. No QAOA, circuits, IBM
integration, customer data, or product UI was part of M2.
M3 adds local ideal standard-QAOA with a uniform initial state, X mixer,
exact V2 statevector training, COBYLA, seeded final sampling and a frozen
multi-restart depth study. The M1/M2 mathematical references stay unchanged.
The completed study contains 660 runs on 22 frozen references and 844 passing
tests. All 14,246 eligible basis-state energies match Qiskit exactly. QAOA raises
ground probability above uniform in 577 runs; 83 remain below it. The bounded
slack case stays below uniform at every tested depth. See the
[M3 completion report](docs/m3_completion_report.md) for complete restart,
sampling, depth and structural results.
The research roadmap remains in [RESEARCH_PLAN.md](RESEARCH_PLAN.md).

## Run locally

Install [uv](https://docs.astral.sh/uv/), then use the locked Python 3.12 environment:

```sh
uv sync --locked --all-groups
uv run --locked python -m pytest
uv run --locked ruff check .
uv run --locked mypy
```

Runtime dependencies include OR-Tools, Pydantic, Qiskit SDK 2.5.2, SciPy,
NumPy and Matplotlib; pytest, Ruff and mypy are development tools. Exact
resolved versions are committed in `uv.lock`. No IBM Runtime package or
credentials are needed.

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
  qubo/                 Exact encoding/decoding, Ising, enumeration and verification
  qaoa/                 Verified SDK adapter, ideal training, sampling and M3 study
tests/                  Mathematical, solver, reproducibility and workflow tests
configs/                Baseline and easy/tight generator configurations
data/synthetic/         Synthetic families and ten hand-verifiable fixtures
docs/                   Model, experiment protocol and implementation decision
```

## M2 equivalence workflow

```sh
uv run --locked python -m km_quantum qubo encode --instance data/synthetic/m2/mini_example.json --config configs/m2_mini_example.json --output results/m2-mini-qubo.json
uv run --locked python -m km_quantum qubo verify --instance data/synthetic/m2/mini_example.json --model results/m2-mini-qubo.json --benchmark-output results/m2-mini-benchmark.jsonl --experiment-id m2-mini
uv run --locked python -m km_quantum qubo to-ising --model results/m2-mini-qubo.json --output results/m2-mini-ising.json
uv run --locked python -m km_quantum qubo acceptance --output results/m2-acceptance.json --benchmark-output results/m2-acceptance.jsonl
```

The gate checks fourteen feasible hand cases and at least one hundred frozen
random cases, every ground state, canonical objective extensions, and exact
Ising energies for every bitstring. It separately recognizes original
infeasibility and demonstrates expected failures from unsafe penalties.
Complete records use a distinct M2 schema; existing M1 commands/records retain
their behavior. Exhaustive enumeration is limited by an explicit state cap.

See [the QUBO formulation and penalty proof](docs/qubo_formulation.md),
[the equivalence protocol](docs/m2_equivalence_protocol.md), and
[the complete 16-state worked example](docs/m2_mini_example.md).
The [frozen QUBO/Ising and exact ground-state artifacts](data/synthetic/m2/frozen_qubos.jsonl)
contain all 114 safe reference models for the next milestone.
Verified counts, tooling results and repository changes are in the
[M2 completion report](docs/m2_completion_report.md).

## M3 ideal-QAOA workflow

```sh
uv run --locked python -m km_quantum qaoa manifest
uv run --locked python -m km_quantum qaoa verify-operator --output results/m3/operator-check.json
uv run --locked python -m km_quantum qaoa inspect --reference H_workload --p 2
uv run --locked python -m km_quantum qaoa run --reference H_workload --p 2 --seed 4 --output results/m3/example.json
uv run --locked python -m km_quantum qaoa sample --run results/m3/example.json --shots 4096 --seed 42 --output results/m3/example-samples.json
uv run --locked python -m km_quantum qaoa benchmark --output-directory results/m3
uv run --locked python -m km_quantum qaoa acceptance --output-directory results/m3 --tooling results/m3/tooling.json
```

The recovered M2 checkout omitted its ignored acceptance report. M3 reconstructed
it once using unchanged M2 code and preserves those exact bytes in this branch;
provenance distinguishes that reconstruction from historical evidence. Keep
`results/m2-acceptance.json` unchanged; use a separate output for new M2 audits.
Benchmark execution requires a clean code commit. The immutable manifest fixes
22 structural references, depths 1/2/3 and ten starts: 660 runs. All 113 models
within the 14-qubit cap participate in complete basis-state correctness gates.
Use `--split development` or `--split evaluation` for staged execution and
`--resume` to retain verified completed runs. Run outputs retain every
optimizer evaluation, ideal sampling counts and environment identity.

Ground probability sums every exact M2 minimum. Decoded M1 feasibility and
optimality are reported separately from auxiliary-encoding feasibility:
an optimal business schedule can have incorrect slack bits. Raw decoded
optimal-schedule probability can therefore exceed exact ground probability.
All energies restore the frozen Ising offset. Results include full restart
distributions and reproducible plots, without selecting favorable seeds.

See [the ideal experiment protocol](docs/m3_experiment_protocol.md) and
[the M3 completion report](docs/m3_completion_report.md). The next milestone
is **M4 — Noise + Warm-Start QAOA**, described only. M3 uses no hardware,
noise models, warm starts or quantum-advantage claims.

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
