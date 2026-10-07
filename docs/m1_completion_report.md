# M1 completion report

Verified locally on 2026-10-07 on branch `feat/m1-classical-baseline`.
The repository was cloned into `D:/Quanten computing`; M0's research contract
was preserved. This report records an implementation and correctness audit,
not a performance study or quantum advantage claim.

## Implemented

- Python 3.12 project with src layout, uv lock and editable local installation.
- Pydantic domain, objective/search configuration, schedule and result schemas.
- Deterministic, versioned synthetic generator; easy/tight families; ten
  hand-derived JSON cases, including impossible mandatory coverage.
- Independent structured validator for C1–C5, IDs, coverage and malformed input.
- Independent integer objective evaluator with explicit component breakdown.
- CP-SAT with optional fixed intervals, NoOverlap, workload limits, explicit
  coverage, seed, worker count and time budgets; independently checked results.
- Exhaustive reference solver with an explicit state cap and honest proof status.
- JSON/JSONL benchmark schema, raw artifacts, hashes and environment/Git provenance.
- Scriptable generate/solve/validate/benchmark CLI and model/protocol documentation.

No QUBO, QAOA, IBM integration, customer data or product UI was added.

## Mathematical model

Binary `x_jw` chooses a worker and binary `u_j` explicitly omits a job.
Coverage is `sum_w x_jw + u_j = 1`; mandatory jobs additionally require `u_j=0`.
Skills and full availability containment restrict eligible pairs. A worker's
selected half-open job intervals cannot overlap. Its total selected minutes
cannot exceed `max_work_minutes`.

Minimize priority-weighted omission penalties plus optional per-assignment
rounded costs plus optional workload range across all workers:

```text
sum_j penalty_j * priority_j * u_j
+ cost_weight * sum_jw ceil(hourly_rate_w * duration_j / 60) * x_jw
+ imbalance_weight * (max_w load_w - min_w load_w)
```

The default omission penalty is 100000; the other weights default to zero.
This is a weighted sum. Complete equations, rounding and boundary semantics
are in [model.md](model.md).

## Verification and tests

The final complete suite produced **207 passed in 6.17 s**, with no failures
or warnings. Lint, formatting and source type checking also passed:

```text
uv run --locked python -m pytest -q       207 passed
uv run --locked ruff check .             All checks passed
uv run --locked ruff format --check .    27 files already formatted
uv run --locked mypy                     No issues in 17 source files
uv build                                Wheel and source distribution built
```

On this Windows machine uv was installed with `py -3.13 -m pip install --user uv`
and invoked as `py -3.13 -m uv` because its script directory was absent from
the existing shell PATH. The project environment itself uses Python **3.12.15**,
OR-Tools **9.15.6755**, Pydantic **2.13.5** and pytest **9.1.1** from `uv.lock`.

Checks include malformed/frozen schemas, serialization, unknown/duplicate IDs,
mandatory coverage, every hard constraint, boundary and split-window behavior,
manual objective arithmetic, per-assignment rounding, empty dimensions,
generator identity across processes/hash seeds, consistent proof statuses,
interrupted search, state caps, large integer safeguards and the full CLI.

CP-SAT and exhaustive enumeration agree on all ten manually derived references
and 36 additional mixed-objective small comparisons. A separate test-only
plain-Python enumerator, which uses no production validator or evaluator,
agrees with CP-SAT on **150 frozen development seeds**. An additional local
numeric audit checked 12 large, representable integer objective cases.
These checks support the defined model; they do not establish universal
performance or correctness for every conceivable input.

## Benchmark examples

These are one-run smoke examples on the local Windows host, not distributions.
Records refer to the clean implementation commit
`88aed621e098e3286e42507597c90d5c6013ca98` and include complete inputs,
configuration and dependency/environment metadata.

| Run | Workers / jobs | Objective | Status | Runtime ms | Proven optimal | Assigned / omitted |
| --- | --- | ---: | --- | ---: | --- | --- |
| CP-SAT medium | 25 / 40 | 1600000 | OPTIMAL | 71.2659 | yes | 25 / 15 |
| CP-SAT tiny | 2 / 5 | 300000 | OPTIMAL | 8.0846 | yes | 2 / 3 |
| Exhaustive tiny | 2 / 5 | 300000 | OPTIMAL | 7.6675 | yes | 2 / 3 |

All three schedules were independently valid, with gap zero and no violations.
Runtime includes the solver's model/enumeration and independent result checks;
it excludes CLI IO and benchmark provenance collection.

Reproduce the medium instance and the workflow:

```sh
uv run --locked python -m km_quantum generate --workers 25 --jobs 40 --skills 4 --compatibility-density 0.75 --overlap-density 0.25 --workload-tightness 1.2 --seed 42 --output results/m1-medium-instance.json
uv run --locked python -m km_quantum solve --instance results/m1-medium-instance.json --config configs/baseline.json --output results/m1-medium-solution.json
uv run --locked python -m km_quantum validate --instance results/m1-medium-instance.json --schedule results/m1-medium-solution.json
uv run --locked python -m km_quantum benchmark --instance results/m1-medium-instance.json --config configs/baseline.json --experiment-id m1-medium-example --output results/m1-medium-benchmark.jsonl
uv run --locked python -m km_quantum benchmark --instance data/synthetic/tight.json --solver exhaustive --experiment-id m1-tiny-oracle-example --output results/m1-tiny-benchmark.jsonl
```

Local outputs are retained under ignored `results/`, including both tiny
benchmark records. Runtime and timestamps will differ on repetition.

## Repository changes

README.md was updated; all remaining files below were newly created. The
research plan was read in full and remains unchanged.

```text
.gitignore
.python-version
README.md
pyproject.toml
uv.lock
configs/baseline.json
configs/generator_easy.json
configs/generator_tight.json
data/synthetic/easy.json
data/synthetic/tight.json
data/synthetic/hand_cases/cost_choice.json
data/synthetic/hand_cases/mandatory_infeasible.json
data/synthetic/hand_cases/multiple_optima.json
data/synthetic/hand_cases/no_compatible_worker.json
data/synthetic/hand_cases/priority_overlap.json
data/synthetic/hand_cases/split_availability.json
data/synthetic/hand_cases/touching_intervals.json
data/synthetic/hand_cases/unavailable_job.json
data/synthetic/hand_cases/unique_skills.json
data/synthetic/hand_cases/workload_limit.json
docs/benchmark.schema.json
docs/decisions/m1_implementation_plan.md
docs/experiment_protocol.md
docs/model.md
docs/m1_completion_report.md
src/km_quantum/__init__.py
src/km_quantum/__main__.py
src/km_quantum/domain/__init__.py
src/km_quantum/domain/models.py
src/km_quantum/experiments/__init__.py
src/km_quantum/experiments/benchmark.py
src/km_quantum/experiments/io.py
src/km_quantum/instances/__init__.py
src/km_quantum/instances/generator.py
src/km_quantum/instances/hand_cases.py
src/km_quantum/metrics/__init__.py
src/km_quantum/metrics/objective.py
src/km_quantum/metrics/validation.py
src/km_quantum/solvers/__init__.py
src/km_quantum/solvers/classical/__init__.py
src/km_quantum/solvers/classical/cp_sat.py
src/km_quantum/solvers/classical/exhaustive.py
tests/test_domain_metrics.py
tests/test_independent_oracle.py
tests/test_instances.py
tests/test_solvers.py
tests/test_workflow.py
```

Changes were committed by topic. Final review checked the mathematical model,
numeric limits, honest status/bounds, dependency lock, source diff, synthetic
artifacts, whitespace and common credential patterns. No secrets were found.

## Known limitations

Fixed job intervals only; no dynamic repair, routing/travel, overtime, labor-law
model, continuous starts, UI or quantum implementation. The synthetic generator
uses single-skill requirements and full-horizon worker availability; separate
fixtures and independent audit tests cover multiple skills and restricted
periods. Density parameters describe a distribution/construction, not an exact
realized density or feasibility guarantee.

Exhaustive enumeration is exponential and capped; its soft wall limit is checked
between candidates. CP-SAT time limits can yield FEASIBLE or UNKNOWN instead of
proof. Single-worker seeds improve repeatability, but timestamps, elapsed time,
wall-budget cutoffs and tied optimal choices are not universally bit-reproducible.
CP-SAT conservatively rejects numeric aggregates above `2**53 - 1`; exhaustive
uses exact Python integers and omits a floating bound when necessary.

## Next milestone

**M2 — QUBO mapping + equivalence verification.** Define the mapping and penalty
strategy, then exhaustively verify on tiny instances that encoded ground states
correspond to feasible original optima with the same business objective.
M2 has not been implemented.
