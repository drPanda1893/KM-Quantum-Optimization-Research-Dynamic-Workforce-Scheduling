# M1 experiment protocol

M1 establishes a reproducible classical reference for synthetic fixed-slot
scheduling. A result only serves as ground truth when its incumbent is
independently valid and its optimality is proven. A feasible time-limited
incumbent is useful evidence but cannot be called the optimum. M1 contains no
quantum implementation or quantum-advantage claim.

## Environment and artifacts

Use Python 3.12 and install the exact dependency graph from `uv.lock`:

```sh
uv sync --locked --all-groups
uv run --locked python -m pytest
uv run --locked ruff check .
uv run --locked mypy
```

The package uses OR-Tools CP-SAT, Pydantic, and the standard library. The lock
may contain NumPy as an OR-Tools transitive dependency; M1 does not require
direct NumPy programming. Freeze a clean Git commit before a reported study.
Keep that commit's dependency lock, full instance files, solver configurations,
raw records, and command/environment details. A dirty working tree is recorded
but its edits cannot be reconstructed from its SHA; commit or archive those
changes before claiming reproducibility. The full snapshot of instance and
configuration in each record protects against later file edits.

## Instance generation and seeds

`generate_instance(GeneratorConfig(...))` uses a local `random.Random(seed)`.
It does not use global random state or timestamps. Identical normalized
parameters and generator version yield identical serialized instance content
and instance ID within the pinned Python environment. Persist the actual JSON
and canonical content hash; do not rely on a seed alone. Future changes to the
generator require a new version and separate dataset identity.

All generator parameters are recorded under `generator_parameters`:

| Parameter | Default | Defined meaning |
| --- | ---: | --- |
| `number_of_workers` | 3 | Worker count; zero is supported |
| `number_of_jobs` | 4 | Job count; zero is supported |
| `number_of_skills` | 2 | Size of synthetic skill universe, at least one |
| `compatibility_density` | 0.75 | Independent probability a worker owns each skill |
| `overlap_density` | 0.25 | Probability a subsequent job reuses an earlier start |
| `workload_tightness` | 0.5 | Target job demand / aggregate worker capacity |
| `priority_distribution` | `[0.6,0.3,0.1]` | Relative weights for priorities 1, 2, 3 |
| `seed` | 0 | Nonnegative generator random seed |
| `min_duration_minutes` | 30 | Inclusive minimum duration |
| `max_duration_minutes` | 60 | Inclusive maximum duration |
| `horizon_minutes` | null | Auto: `max(1,jobs)*max_duration_minutes` |
| `must_assign_fraction` | 0 | Independent probability a job requires coverage |
| `min_cost_per_hour` / `max_cost_per_hour` | null | Inclusive cost range, both present or both absent |

Each generated job requires one uniformly sampled skill. Compatibility density
is the expected fraction of skill-compatible worker/job pairs, not a guaranteed
observed fraction or a feasibility guarantee. Metadata records the observed
skill compatibility and pairwise overlap densities.

The first job starts at zero. A subsequent job either shares a randomly chosen
earlier start, or starts at the maximum existing end. At overlap density zero,
jobs do not overlap; at one, every job starts at zero. Intermediate probabilities
are construction parameters and do not equal the realized fraction of
overlapping pairs. Durations are sampled uniformly over integer bounds. An
explicit horizon must be at least `max(1,jobs)*max_duration_minutes`, ensuring
that even the sequential construction fits. Every generated worker is available
for the entire horizon. Hand cases exercise split/restricted availability.

For positive workload tightness `t`, each worker's capacity is
`min(horizon, ceil(total_job_minutes/(workers*t)))`. At zero the capacity is
the horizon. Values above one intentionally restrict aggregate capacity,
subject to integer rounding. A low target does not eliminate skill or overlap
infeasibility. `must_assign_fraction=0` allows omission of every job; setting it
to one requires all jobs and may make the instance infeasible. Cost rates, when
enabled, are integers in minor monetary units/hour. Priority weights need not
sum to one, but must be finite, nonnegative, and have a positive total.

The checked-in `configs/generator_easy.json` and `configs/generator_tight.json`
define contrasting synthetic families; their generated JSON is under
`data/synthetic/`. Ten hand-derived fixtures under `data/synthetic/hand_cases/`
are validation examples, not a held-out performance benchmark. Partition larger
benchmark suites into development, validation, and final test sets before
configuration tuning. Freeze the final test seeds and protocol before looking
at their outcomes. Report all attempted instances, including infeasible ones.

## Solver configuration and reproducibility

`configs/baseline.json` records the default objective and search parameters:

```json
{
  "objective": {"unassigned_penalty": 100000, "cost_weight": 0, "imbalance_weight": 0},
  "time_limit_seconds": 10.0,
  "num_search_workers": 1,
  "random_seed": 0,
  "max_deterministic_time": null,
  "exhaustive_max_states": 1000000
}
```

The generator seed and solver seed are separate. CP-SAT uses one search worker
by default, explicit seed, and explicit wall-clock limit. Multiworker search can
increase throughput but can change results between runs; always report the
worker count. A pinned single-worker environment and deterministic work budget
reduce variability but do not guarantee bit-identical JSON: runtime, timestamps,
machine speed, wall-time cutoffs, and tied optimal assignments can differ.
`max_deterministic_time` is an optional OR-Tools work budget, not seconds of
wall time; both work and wall limits apply when it is set. Set a generous wall
limit when studying deterministic-work behavior. Exact reproducibility claims
must distinguish instance content, achieved objective/proof, and elapsed time.

The exhaustive oracle uses the same objective and wall limit, but explicitly
ignores CP-SAT-only `max_deterministic_time`, search-worker count, and random
seed. It enumerates in stable input order. The state guard rejects requests
larger than the configured cap instead of pretending an incomplete search is
exact. Its time limit includes candidate creation, validation, and scoring.
Only use it for tiny cases.
Its wall limit is checked between candidates, so evaluating a final candidate
can extend elapsed time past the limit. An exhaustive optimum above
`2**53 - 1` retains its exact integer objective and proof, with a null bound
rather than an inexact floating-point conversion.

`runtime_ms` is local elapsed wall time for the solver call, including model
construction/enumeration and independent result checks. It excludes CLI input
reading, JSON writing, and benchmark provenance collection. CP-SAT metadata
separately records model-build time, search wall time, deterministic time,
branches, conflicts, and variable counts. Thus this metric must not be presented
as total CLI/end-to-end latency. Record external end-to-end timing separately
when that is the chosen comparison metric.

For a returned incumbent `F` and minimization lower bound `b`, the reported
relative gap is `max(0,(F-b)/max(1,abs(F)))`. Proven optima report gap zero and
their exact independently evaluated objective as bound; the original CP-SAT
floating-point bound is retained in raw metadata. Without an incumbent, objective
and gap are null. `UNKNOWN` can still report a lower bound; `INFEASIBLE` has no
feasible objective. Exhaustive interrupted runs use the nonnegative objective's
zero lower bound. No missing value means zero by implication.

## CLI workflow and errors

Commands emit one JSON object to stdout. `solve` writes a `SolverResult` JSON;
`validate` accepts either a plain schedule or that result. `benchmark` appends
one complete record per line. CLI parameter overrides apply after loading a
configuration file and the effective configuration is recorded.

```sh
uv run --locked python -m km_quantum generate --config configs/generator_easy.json --seed 42 --output data/synthetic/run-42.json
uv run --locked python -m km_quantum solve --instance data/synthetic/run-42.json --config configs/baseline.json --output results/run-42.json
uv run --locked python -m km_quantum validate --instance data/synthetic/run-42.json --schedule results/run-42.json
uv run --locked python -m km_quantum benchmark --instance data/synthetic/run-42.json --config configs/baseline.json --experiment-id m1-example --output results/m1-example.jsonl
```

For tiny instances add `--solver exhaustive` to solve/benchmark. Use `--help`
for parameter options. `--priority-distribution 0.6,0.3,0.1` supplies relative
sampling weights. `--random-seed` configures search and `--seed` configures
generation. Keep instance and result output paths distinct from the inputs.

Exit code 0 means generation succeeded or the returned/validated schedule is
feasible. Exit code 1 means no schedule was returned or the candidate is invalid;
solver results and benchmark records are still saved for infeasible/unknown
runs. Exit code 2 means usage, input-schema, JSON, or file errors, emitted as a
structured `error` object on stderr. A result with `schedule=null` cannot pass
validation. Feasible does not imply proven optimal. JSONL append is a serial
writer; use separate files per process rather than simultaneous appenders.

## Benchmark result schema (version 1)

The machine-readable shape is [benchmark.schema.json](benchmark.schema.json),
generated from the `BenchmarkResult` Pydantic model. The model additionally
checks finite numeric values, aware timestamps, and proof/coverage consistency.

| Fields | Purpose |
| --- | --- |
| `experiment_id`, `instance_id`, `seed` | Experiment and synthetic input identity |
| `instance_hash` | SHA-256 of full normalized, sorted compact instance JSON |
| `solver`, `solver_version`, `status`, `proven_optimal` | Algorithm, version, outcome and proof |
| `feasible`, `objective`, `best_bound`, `optimality_gap` | Independently checked quality |
| `runtime_ms` | Solver-call local wall time as defined above |
| `number_workers`, `number_jobs` | Instance size |
| `assignment_count`, `unassigned_count`, `violations` | Coverage and validator diagnostics |
| `timestamp` | Aware UTC execution timestamp |
| `git_commit`, `git_dirty` | Checkout SHA and modification indicator, nullable outside Git |
| `dependency_lock_sha256`, `environment` | Lock identity, Python/platform and package versions |
| `instance`, `solver_config`, `raw_result`, `objective_breakdown` | Full input, effective configuration, solver output and independent score |

`schema_version` is 1. When no schedule exists, `feasible=false`, objective and
coverage counts are null, objective breakdown is null, and violations are empty
because there is no candidate to validate. This differs from a valid schedule
that deliberately leaves jobs unassigned. Do not infer feasibility from status
or an empty violation list alone. Bounds remain nullable. A hash identifies
content; it does not replace saving that content. UTC time is run provenance,
not a generator input. Git collection may be unavailable in installed wheels;
in that case Git fields are null.

## Verification and future fair comparisons

Tests independently check schemas, raw-candidate validation, manual objective
arithmetic, deterministic generation, serialization, and CLI persistence. For
all ten hand cases and additional tiny generated instances, compare CP-SAT and
complete exhaustive enumeration on feasibility and exact optimal objective.
Tied optimum schedules may differ. Test the tiny reference cases with ample
budgets; a timeout cannot verify optimum equivalence.
Additionally, `tests/test_independent_oracle.py` freezes 150 development seeds
and uses a separate plain-Python enumerator with its own feasibility and
objective calculations, independent of production metrics and exhaustive code.
This audits shared evaluator/validator assumptions, not performance scaling.

Freeze metric definitions, objectives, instance families, seeds, and compute
budgets before benchmarking. Compare future methods against CP-SAT on exactly
the same instance and the same public validator/evaluator. Include classical
preprocessing, parameter optimization, postprocessing, repair, and validation
in any end-to-end comparison. State whether the budget is wall time, evaluations,
deterministic work, or another resource. Separate infrastructure queues from
algorithm time, while reporting total elapsed time as well. Use repeated
independent seeds for stochastic methods; report counts, feasibility rate,
median, spread, and all failed attempts, not only the best run. Bounds and
proven optima must remain distinct from best-known incumbents.

The next milestone is **M2 — QUBO mapping plus equivalence verification**:
define binary/QUBO variables and penalties, then enumerate tiny original and
encoded problems to verify that encoded ground states correspond to feasible
original optima with the same business objective. M2 is not implemented in M1.
