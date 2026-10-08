# M2 equivalence protocol and acceptance gate

M2 verifies a transformation, not quantum performance. Keep the M1 definitions,
validator, evaluator, CP-SAT and exhaustive reference unchanged. M2 does not use
QAOA, circuits, QPUs, IBM Runtime, warm starts, a hybrid optimizer, or customer
data. The [formulation](qubo_formulation.md) gives the penalty proof; the
[mini example](m2_mini_example.md) exposes every state of one complete mapping.

## Three exact references and every minimum

For each feasible case:

1. Run M1 CP-SAT and M1 exhaustive enumeration with the same objective weights.
   Require both to prove the same optimum; a time-limited incumbent cannot
   establish equivalence.
2. Encode the instance deterministically and enumerate every QUBO bitstring
   within the declared state cap. Retain every equal-energy minimum.
3. Decode every minimum literally and check it with the unchanged M1 validator.
   Check every encoded constraint component is zero, and independently score
   each schedule with the M1 evaluator.
4. Require each ground state's business energy, decoded objective and total
   energy to match the proven classical optimum. Require no encoded invalid
   state to have energy equal to or below ground.
5. For every valid primary assignment/unassigned schedule, construct its
   canonical slack/extrema extension and check its QUBO energy against M1.
   This verifies the objective's lower-envelope property beyond the ground state.
6. Convert to exact scaled-integer Ising and check `energy4(spins)=4*Q(bits)` on
   **every** state, including constants and all invalid states.

There is no candidate filtering that could hide an invalid tied minimum.
Bounded-integer duplicate encodings can create multiple bitstrings for one
schedule; all are retained. Encoded feasibility includes auxiliary equations;
a decoded-valid schedule with bad slack bits is an invalid encoding. A
zero-penalty noncanonical imbalance extension can have an inflated business
cost; it is not asserted to equal M1 pointwise, and cannot be a safe ground state.

The exact polynomial solver has no truncated-success mode. `max_states` rejects
models with too many states before enumeration; `max_variables` guards encoding
allocation. A guard error is input/resource failure, not PASS. CP-SAT numeric
guards still apply, so unbounded Python-integer QUBO algebra does not expand the
M1 oracle's supported range silently.

## Frozen synthetic suite

`qubo.references.m2_hand_cases()` supplies fourteen safe, manually specified
feasible cases: one worker/job, two workers/one job, skill pruning, availability
pruning, overlap, unavoidable omission, priority selection, workload, multiple
optima, combined constraints, bounded slack, active imbalance, coupled capacity/imbalance, and a zero job
penalty override. Each carries a hand-derived expected objective.

`random_small_cases(100)` creates one hundred feasible tiny instances from
frozen indexed seeds, using one to three workers, one to four jobs, small
integer durations and bounded capacities. Workers and jobs are sampled directly.
Optional jobs permit a feasible schedule through explicit omission; mandatory
jobs occur only in the two-worker, one-minute-job microcases with guaranteed
skills, availability and capacity. There is no resampling or filtering based on
solver output. Include all generated cases. The named cases cover deliberately
hard structures beyond the random distribution. These are correctness/development
cases, not a held-out scaling or performance study. Save the generated input
snapshots and effective configurations; seeds alone are insufficient provenance.

[`data/synthetic/m2/frozen_qubos.jsonl`](../data/synthetic/m2/frozen_qubos.jsonl)
freezes all 114 safe suite models, their exact Ising representations, all ground
bitstrings, energy levels and invalid margins. Records use canonical sorted
JSON keys, omit runtime-dependent fields, and carry instance/QUBO/Ising hashes.
A drift test re-encodes the saved input/configuration snapshots and requires
identical artifacts. These are the versioned mathematical references for M3.

A separate mandatory-infeasible case has no eligible worker. Both original
oracles must report infeasibility, and the verifier reports `INFEASIBLE` rather
than PASS. A binary polynomial always has a ground state even when the original
problem has no valid schedule; those concepts remain separate.

## Penalty sensitivity, with expected failures

Case L has one mandatory one-minute job and one capable worker. The rate 6000
minor units/hour makes the assignment cost 100 with cost weight one. Because
omission is forbidden, the valid business bound is 100 and safe P is 101.
At scales `1/4` and `1/2`, the applied penalty is 26 or 51. Selecting neither
assignment nor omission violates coverage but costs only that penalty, below
the valid objective 100: an actual invalid global minimum. Verification must
report **FAIL**, and the report preserves those ground states.

At scales 1, 2 and 4, P is 101, 202 and 404. Every invalid state is more
expensive than the valid optimum and equivalence must pass. Run all five scales
without tuning on their observed results. This is explicitly sensitivity
analysis; expected unsafe failures are not counted as safe-case failures or
silently dropped. An arbitrary unsafe configuration may happen to pass a tiny
instance while lacking the mathematical guarantee.

## CLI and automated acceptance

Existing M1 commands and schema-version-1 records remain intact. M2 adds:

```sh
uv run --locked python -m km_quantum qubo encode --instance data/synthetic/m2/mini_example.json --config configs/m2_mini_example.json --output results/m2-qubo.json
uv run --locked python -m km_quantum qubo inspect --model results/m2-qubo.json --matrix
uv run --locked python -m km_quantum qubo solve-exhaustive --instance data/synthetic/m2/mini_example.json --model results/m2-qubo.json --output results/m2-ground.json
uv run --locked python -m km_quantum qubo verify --instance data/synthetic/m2/mini_example.json --model results/m2-qubo.json --output results/m2-verify.json --benchmark-output results/m2-records.jsonl --experiment-id m2-mini
uv run --locked python -m km_quantum qubo to-ising --model results/m2-qubo.json --output results/m2-ising.json
uv run --locked python -m km_quantum qubo acceptance --output results/m2-acceptance.json --benchmark-output results/m2-acceptance.jsonl
```

`verify` accepts either `--model` or `--config`; without either it encodes the
safe default configuration. Commands print one JSON object; `--help` prints
usage text. Encode/inspect/Ising conversion return zero on success. Verification
returns zero only on equivalence PASS; FAIL or original INFEASIBLE returns one
and still saves records. Exact solve returns one when any minimum is an invalid
encoding/schedule, despite the polynomial optimum being proven. Usage, file,
schema, mismatch, or state-cap errors return two with structured stderr JSON.

The acceptance command requires at least 100 random cases. It reports overall
`M2 EQUIVALENCE: PASS` in machine-readable `m2_equivalence` only if every feasible
hand/random case passes full M1/QUBO/Ising checks, the separate infeasible case
is honestly recognized, and all five sensitivity outcomes match the prescribed
expectations. It reports case counts, total enumerated bitstrings, ground-state
counts, per-case metrics/margins and sensitivity outcomes. All optional JSONL
records contain complete inputs and raw verification results. JSONL appends
are serial; use one output per process for parallel execution.

## Spectrum and structure metrics

Record ground energy/degeneracy, first excited energy and gap, every spectrum
level with multiplicity, encoded-valid/invalid counts, decoded-valid/invalid
counts, decoded-valid auxiliary violations, noncanonical imbalance encodings,
best valid and invalid energies and
`constraint_energy_margin=best_invalid_energy-ground`.
If no invalid state exists, its energy and margin are null. Original-infeasible
instances have invalid ground states, which are reported rather than repaired.

Record original worker/job combinations, assignment/unassigned/slack/auxiliary
bit counts, total bits, nonzero linear terms/couplings, pair density, pruning
count and ratio, maximum/minimum nonzero coefficient magnitudes and their exact
rational dynamic range. Coefficient range excludes the constant. Distinguish
assignment count after pruning from total binary variables after auxiliary
allocation. A small number of jobs does not imply a small QUBO.

## M2 JSONL artifact schema

`M2BenchmarkResult` uses `schema_version=2` and `artifact_type=m2_equivalence`;
M1's `BenchmarkResult` stays version one. Top-level M2 fields include:

The machine-readable JSON schema is
[m2_benchmark.schema.json](m2_benchmark.schema.json). Pydantic additionally
checks aware timestamps, finite metrics, ground-state counts and PASS consistency.

| Field group | Contents |
| --- | --- |
| Identity | experiment/instance IDs, seed, full instance hash and QUBO hash |
| Outcome | status, equivalence and Ising flags, classical and decoded objectives |
| Size | original combinations, assignment/unassigned/slack/auxiliary/total bits, terms, density and pruning |
| Coefficients | max/min magnitudes, exact rational dynamic range, complete penalty configuration |
| Spectrum | ground energy/count, valid/invalid minimum counts, best valid and invalid energies/margin, first excited energy/gap, enumerated bitstrings |
| Provenance | UTC aware timestamp, Git SHA/dirty indicator, lock SHA-256, Python/platform/package/encoder versions |
| Raw artifacts | full instance, QUBO configuration/model, size metrics, complete verifier output with both M1 results and every ground state |

`runtime_ms` measures the verifier call including the two original oracles,
QUBO enumeration, canonical-extension checks, and full Ising equality check.
It excludes CLI reading/writing and provenance collection. This differs from
M1's solver-call metric; do not compare the values as if they measured the same
operation. Dirty-checkout provenance does not reconstruct edits: freeze a clean
commit for reported studies. Git fields may be null outside a checkout.

## Test and research discipline

Run the complete M1+M2 pytest suite, Ruff, formatting, mypy and build. Tests
check deterministic mapping/serialization, manual algebra expansions, all small
coverage/overlap states, workload reachability and capacity-floor boundaries,
literal decoding, derived bounds, deliberate unsafe minima, every degenerate
minimum, hand/random oracle agreement, large integer coefficients, exact Ising
state energies, and CLI/record persistence. Property-style loops use fixed
standard-library seeds; no additional dependency is required.

Report exact counts and failures, coefficient/variable growth, proof status and
the expected sensitivity failures. Do not equate a generated model with an
equivalence proof. M2 evidence concerns the documented original model and tiny
fully enumerated cases, supported by its penalty argument; it makes no claim
about algorithmic advantage, quantum noise, circuit depth, or QPU suitability.

The next milestone is **M3 — Ideal QAOA**: reuse frozen M2 QUBOs on ideal
simulators and compare all outputs against their exact ground-state references.
M3 is described only and is not implemented here.
