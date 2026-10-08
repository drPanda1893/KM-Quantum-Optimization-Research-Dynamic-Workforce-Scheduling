# M2 completion report

**M2 EQUIVALENCE: PASS.** Completed on 2026-10-08 on
`feat/m2-qubo-mapping`. Every safe hand/random ground state preserves the
frozen M1 optimum. All states preserve QUBO-to-Ising energy exactly.

The classical reference remains commit
`7f6fbcc881fb4070aad0d54230e63e8fc9f7c04c` on
`feat/m1-classical-baseline`. The final acceptance study ran from clean code
commit `1b0f6de8382651e02bf0708730e0a374a10511ed`; subsequent changes are
documentation only. All 120 JSONL records report that SHA and `git_dirty=false`.

## Implemented

- Canonical binary mapping and reason-coded pruning of impossible assignments.
- SDK-independent integer polynomial, upper-triangular matrix, deterministic
  JSON serialization, named energy components and structure/coefficient metrics.
- Exact coverage, mandatory coverage, overlap and workload constraints, bounded
  binary slack, and an exact auxiliary formulation of M1's maximum-minus-minimum
  workload objective, including idle workers.
- Derived penalties with rational scaling and explicit safety metadata.
- Literal decoder and canonical schedule-to-bitstring extension, without repairs.
- Complete QUBO enumeration, all equal minima, spectrum, first excited gap,
  encoded/decoded feasibility counts, best valid/invalid energy and invalid margin.
- Verification against unchanged CP-SAT, M1 exhaustive solver, original-choice
  enumeration and the independent M1 validator/objective evaluator.
- Exact Ising conversion and all-state equality verification.
- M2 CLI and acceptance gate; a separate version-two benchmark schema with full
  inputs, models, ground states, oracle results and provenance.
- Fourteen feasible hand cases, 100 frozen seeded cases, an original-infeasible
  case, deliberate unsafe penalties, a complete worked example and 114 frozen
  QUBO/Ising/ground-state artifacts for the future M3 study.

No existing domain, validator, objective, generator, classical solver, M1
benchmark schema, M1 tests, dependency configuration or lockfile changed.
The existing CLI gains only nine dispatch lines. No dependencies were added.

## Mathematical formulation

Let `x_jw` be an eligible assignment bit and `u_j` the explicit omission bit.
Sort jobs/workers by ID, then allocate assignments, omissions and auxiliary
groups. Bit index zero is the leftmost serialized character. Remove pairs only
for missing skills, absence from one complete availability period, or individual
duration above capacity. Every removed pair is impossible in an M1 solution.

With original priority-weighted omission charges `p_j*q_j`, assignment charge
`c_jw=cost_weight*ceil(rate_w*duration_j/60)`, and normalized range `D`:

```text
Q = sum_j p_j*q_j*u_j + sum_jw c_jw*x_jw + imbalance_weight*g*D
  + P * sum_j (sum_w x_jw + u_j - 1)^2
  + P * sum_mandatory_j u_j
  + P * sum_(w,unordered overlapping j,k) x_jw*x_kw
  + P * sum_active_w (sum_j (duration_j/g_w)*x_jw + S_w - C_w)^2
  + P * sum_w (A - l_w - R_upper,w)^2
  + P * sum_applicable_w (l_w - B - R_lower,w)^2
  + P * (A - B - D)^2
```

Here `g_w` is the GCD of eligible durations for worker w and
`C_w=floor(capacity_w/g_w)`. A workload inequality is omitted only if even all
eligible assignments together fit. `S_w` covers exactly `[0,C_w]` using
`bit_length(C_w)` bounded bits; zero needs no bit. The final bounded weight
replaces the largest ordinary binary weight, so no out-of-range values exist.
Duplicate representations are retained as auxiliary degeneracy.

When imbalance is active, `g` is the global duration GCD, `l_w=load_w/g`,
`H_w=min(floor(capacity_w/g),sum eligible normalized durations)` and
`H=max H_w`. A, D and upper slacks have bounds H; B has bound `min H_w`,
and each lower slack has bound H_w. If `min H_w=0`, B is fixed zero and the
lower equations are redundant. Zero residuals impose A >= every load,
B <= every load, and D=A-B. The minimum over these extensions is exactly
the original workload range. Arbitrary auxiliary choices can have higher
business energy; this is lower-envelope equivalence. With safe P, each valid
schedule's unrestricted auxiliary minimum also equals its M1 objective.

Every square expands with `x_i^2=x_i`. The artifact stores
`Q=c+sum a_i*x_i+sum_(i<k) b_ik*x_i*x_k`, with Q_ii=a_i and Q_ik=b_ik in
an upper-triangular matrix. The full derivation and bounds are in
[qubo_formulation.md](qubo_formulation.md).

## Penalty derivation

Business energy is nonnegative for every bitstring. Every feasible schedule
has a canonical extension with business cost bounded by:

```text
B_upper = sum_j max(allowed omission charge, maximum eligible assignment charge)
          + imbalance_weight*g*H
P_safe = B_upper + margin              default margin = 1
P_applied = ceil(P_safe * Fraction(penalty_scale))
```

The omission entry is zero for mandatory jobs, and the imbalance term vanishes
when inactive. Any violated encoded hard constraint has an integer residual
or selected conflict/mandatory omission and therefore costs at least P.
Thus `P>B_upper` excludes every invalid encoding from every feasible-instance
ground state. This bound uses instance/configuration data, without looking at
the optimum or tuning on observed test results. The strict inequality excludes
invalid ties. Original infeasibility is reported separately, never as PASS.

Sensitivity case L has one mandatory assignment costing 100, so B_upper=100
and P_safe=101. The exact results are:

| Scale | Applied P | Ground energy | Best valid energy | Invalid margin | Verification |
| ---: | ---: | ---: | ---: | ---: | --- |
| 1/4 | 26 | 26 | 100 | 0 | Expected FAIL: invalid ground |
| 1/2 | 51 | 51 | 100 | 0 | Expected FAIL: invalid ground |
| 1 | 101 | 100 | 100 | 1 | PASS |
| 2 | 202 | 100 | 100 | 102 | PASS |
| 4 | 404 | 100 | 100 | 304 | PASS |

Unsafe configurations can pass individual cases empirically without acquiring
a general safety guarantee. Tests also exercise invalid ties and reject PASS.

## Variable growth

The following encoding-only measurements use unchanged M1 `GeneratorConfig`:
seed 42, one skill, full compatibility, no overlaps, workload tightness one,
one-minute jobs, omission weight seven and the displayed imbalance weight.
Capacity per worker is `ceil(jobs/workers)` minutes. The encoding guard is
explicitly raised to 5000 bits for these measurements; none is enumerated here.

| Workers / jobs | Assignment bits | Omission bits | Bits, imbalance off | Slack bits, imbalance on | Other auxiliaries | Total bits, imbalance on | Couplings, imbalance on |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 / 1 | 1 | 1 | 2 | 0 | 0 | 2 | 1 |
| 2 / 2 | 4 | 2 | 8 | 6 | 3 | 15 | 35 |
| 3 / 4 | 12 | 4 | 22 | 18 | 6 | 40 | 210 |
| 5 / 8 | 40 | 8 | 58 | 30 | 6 | 84 | 730 |
| 10 / 15 | 150 | 15 | 185 | 60 | 6 | 231 | 3500 |
| 25 / 40 | 1000 | 40 | 1090 | 150 | 6 | 1196 | 42790 |

Slack counts in the active column include capacity and imbalance slacks.
Assignments grow up to workers*jobs; each job contributes an omission bit.
Each bounded group adds ceil(log2(bound+1)) bits, and squared residuals can
make connectivity dense. Already 40 bits imply 1,099,511,627,776 states.
The default exhaustive cap is 1,048,576 states, and the default encoder guard
is 256 bits. Both reject oversized work before claiming a result.
Machine-readable measurements are locally saved as `results/m2-growth.json`.

## Equivalence results

| Group | Cases | QUBO states completely enumerated | Ground states analyzed | Outcome |
| --- | ---: | ---: | ---: | --- |
| Feasible hand cases | 14 | 34146 | 21 | All PASS |
| Frozen random seeds 0–99 | 100 | 12868 | 120 | All PASS |
| Original mandatory infeasibility | 1 | 2 | 1 | Expected INFEASIBLE |
| Five sensitivity scales | 5 | 20 | 5 | Two expected FAIL, three PASS |
| Complete acceptance gate | 120 | 47036 | 147 | PASS |

The safe hand/random suite has 114 models, 47,014 states and 141 ground
bitstrings. All 141 decode to M1-valid globally optimal schedules with zero
constraint penalties and matching business energy. Invalid ground count is
zero. There are 87 unique-minimum models and 27 models with degeneracy two;
degeneracy can reflect different schedules or bounded-slack representations.
Every best-invalid margin is strictly positive, ranging from 1 to 55.
QUBO sizes range from 1 to 15 bits. All 47,036 acceptance states, including
unsafe and infeasible states, pass exact QUBO/Ising equality with no tolerance.

| Hand case | Bits | Classical objective = ground energy | Degeneracy | Invalid margin |
| --- | ---: | ---: | ---: | ---: |
| A single worker/job | 2 | 0 | 1 | 1 |
| B two workers, rounded costs | 3 | 1 | 1 | 7 |
| C skills | 2 | 0 | 1 | 8 |
| D availability | 2 | 0 | 1 | 8 |
| E overlap | 4 | 7 | 2 | 8 |
| F omission override | 1 | 6 | 1 | 1 |
| G priorities | 4 | 7 | 1 | 22 |
| H workload | 5 | 7 | 2 | 8 |
| I multiple schedules | 3 | 0 | 2 | 8 |
| J combined constraints | 7 | 7 | 1 | 29 |
| K bounded slack | 7 | 7 | 2 | 15 |
| M active imbalance | 10 | 2 | 2 | 8 |
| N zero override | 2 | 0 | 2 | 1 |
| O coupled capacity and imbalance | 15 | 0 | 2 | 16 |

The independent original-choice enumeration checks every valid schedule's
canonical QUBO extension, including nonoptimal schedules. CP-SAT and M1
exhaustive must both prove the same optimum. An incomplete reference gives
INCONCLUSIVE; excess state space is an explicit error, never a truncated PASS.
The 100 random instances are fixed direct samples with optional omission;
mandatory jobs appear only in guaranteed-feasible microcases. No outcome-based
selection, resampling or filtering occurs.

Acceptance artifacts are locally saved in `results/m2-acceptance.json` and
`results/m2-acceptance.jsonl`. All 120 full benchmark records, their QUBO
models and raw verifier results were reloaded successfully through their
Pydantic schemas. The reproducible, runtime-free reference models are committed
in [frozen_qubos.jsonl](../data/synthetic/m2/frozen_qubos.jsonl).

## Complete miniature example

Two one-hour jobs require distinct skills. Worker A costs two units, B three;
only A can serve job A and only B job B. Both jobs permit omission costing ten.
With cost weight one, imbalance zero, the four bits are `(x_A,x_B,u_A,u_B)`.
Pruning removes two of four assignment combinations. No slack is necessary.
Coverage is `x_A+u_A=1` and `x_B+u_B=1`. B_upper=20 gives P=21:

```text
Q = 2*x_A + 3*x_B + 10*u_A + 10*u_B
    + 21*(x_A+u_A-1)^2 + 21*(x_B+u_B-1)^2
  = 42 - 19*x_A - 18*x_B - 11*u_A - 11*u_B
       + 42*x_A*u_A + 42*x_B*u_B

Q_matrix = [[-19,  0, 42,  0],
            [  0,-18,  0, 42],
            [  0,  0,-11,  0],
            [  0,  0,  0,-11]]
```

Exact enumeration of all 16 bitstrings gives unique ground `1100`, energy
five. Decode: job A -> worker A, job B -> worker B, no omissions. M1 validation
passes; M1 objective is 2+3=5; both original oracles prove five. The first
excited energy is 12, gap seven; best invalid energy is 23, margin 18.
Ising substitution yields:

```text
4*H = 134 - 4*z_A - 6*z_B - 20*z_uA - 20*z_uB
          + 42*z_A*z_uA + 42*z_B*z_uB
```

The ground spins are `(-1,-1,+1,+1)` and H=5 exactly. Constants and quarter-unit
coefficients remain exact through the integer denominator four. The full
original problem, all 16 energies, matrix, decode and validation are shown in
[m2_mini_example.md](m2_mini_example.md). The public CLI was also run end to
end on this saved input/configuration; its QUBO, ground, verification and Ising
JSONs round-trip successfully under `results/m2-mini-*.json`.

## Tests and tooling

Python 3.12.15, locked existing dependencies; **625 tests passed** in 19.98
seconds during the final concurrent acceptance/tooling run. This includes the
unchanged 207 M1 tests and 418 additional M2 tests. Checks cover all required
algebra, feasibility, objective, degeneracy, preprocessing, serialization,
penalty, Ising and workflow paths. Extra boundary cases include capacities not
divisible by the duration GCD, mixed durations with an idle worker, giant
integer coefficients, invalid ties, mismatched artifacts, malformed schemas
and incomplete oracle proofs.

| Check | Result |
| --- | --- |
| `uv run --locked pytest -q` | 625 passed |
| `uv run --locked ruff check .` | All checks passed |
| `uv run --locked ruff format --check .` | 48 files already formatted |
| `uv run --locked mypy` | No issues in 27 source files |
| `uv build` | Wheel and source distribution built successfully |
| Public CLI acceptance with 120 saved benchmark records | PASS |
| Mini CLI encode/inspect/solve/verify/to-ising and schema round-trips | PASS |
| M1 core/reference/dependency diff | Empty |

On this host `uv` is invoked via `py -3.13 -m uv`; the project interpreter
remains Python 3.12.15. pytest/CLI also ran directly with the same project
`.venv` interpreter. No external quantum library was needed. Built artifacts
are `dist/km_quantum-0.1.0-py3-none-any.whl` and
`dist/km_quantum-0.1.0.tar.gz`.

## Known limitations

Full state enumeration is exponential. Random cases target tractable
correctness checks and are not a performance benchmark or held-out evaluation.
Pruning reduces assignment bits but auxiliary groups and dense squares can
still grow rapidly. Safe penalties are conservative, and resulting coefficient
ratios can be unfavorable for later hardware scaling. Python integer algebra
is exact; the unchanged CP-SAT numeric limits still restrict oracle verification.

Imbalance is exact as an auxiliary minimum, not as a pointwise objective for
every arbitrary auxiliary bitstring. Infeasible original problems cannot
produce valid schedules; they retain invalid QUBO minima and are recognized
separately. No noise analysis, circuit-depth study, quantum performance claim
or QPU execution has been performed. Only synthetic data is present.

## Repository changes and commits

Only `README.md` and the nine-line CLI dispatch modify existing M1 files.
The new files are listed below (plus this report):

```text
configs/m2_baseline.json
configs/m2_mini_example.json
src/km_quantum/qubo/__init__.py
src/km_quantum/qubo/models.py
src/km_quantum/qubo/encoder.py
src/km_quantum/qubo/decoder.py
src/km_quantum/qubo/exhaustive.py
src/km_quantum/qubo/verification.py
src/km_quantum/qubo/ising.py
src/km_quantum/qubo/references.py
src/km_quantum/qubo/cli.py
src/km_quantum/experiments/m2_benchmark.py
tests/test_qubo_models.py
tests/test_qubo_encoding.py
tests/test_qubo_solver.py
tests/test_qubo_equivalence.py
tests/test_ising.py
tests/test_qubo_workflow.py
docs/decisions/m2_implementation_plan.md
docs/qubo_formulation.md
docs/m2_equivalence_protocol.md
docs/m2_mini_example.md
docs/m2_benchmark.schema.json
data/synthetic/m2/suite_manifest.json
data/synthetic/m2/random_cases.json
data/synthetic/m2/frozen_qubos.jsonl
data/synthetic/m2/mini_example.json
data/synthetic/m2/L_unsafe_penalty.json
data/synthetic/m2/mandatory_infeasible.json
data/synthetic/m2/hand_cases/A_single_job.json
data/synthetic/m2/hand_cases/B_two_workers_cost.json
data/synthetic/m2/hand_cases/C_skill_compatibility.json
data/synthetic/m2/hand_cases/D_availability.json
data/synthetic/m2/hand_cases/E_overlap.json
data/synthetic/m2/hand_cases/F_unassigned_override.json
data/synthetic/m2/hand_cases/G_priority.json
data/synthetic/m2/hand_cases/H_workload.json
data/synthetic/m2/hand_cases/I_multiple_optima.json
data/synthetic/m2/hand_cases/J_combined_constraints.json
data/synthetic/m2/hand_cases/K_bounded_slack.json
data/synthetic/m2/hand_cases/M_active_imbalance.json
data/synthetic/m2/hand_cases/N_zero_override.json
data/synthetic/m2/hand_cases/O_capacity_and_imbalance.json
```

Source/fixture commits, in order:

| Commit | Subject |
| --- | --- |
| `09ea6c4` | feat: add canonical exact QUBO representation and penalty metadata |
| `4482efa` | feat: encode scheduling constraints with bounded slack and exact imbalance |
| `9ba9fea` | feat: enumerate every QUBO ground state and exact energy spectrum |
| `e256b86` | feat: add exact scaled-integer Ising conversion |
| `3466710` | test: verify all QUBO minima and canonical encodings against M1 oracles |
| `938d17b` | feat: add M2 CLI acceptance gate and reproducible benchmark records |
| `402a3b7` | test: freeze 114 QUBO Ising and exact ground-state reference artifacts |
| `890db27` | docs: derive QUBO penalties and document complete equivalence workflow |
| `1b0f6de` | fix: report malformed serialized energy totals as schema errors |

This report and final explanatory documentation are committed with subject
`docs: record M2 acceptance evidence and completion report`. Its SHA is available
through `git log feat/m1-classical-baseline..feat/m2-qubo-mapping`; the report
does not embed its own circular commit hash. Results/build files are ignored
local artifacts; the frozen mathematical references are versioned. No remote
push or pull request is part of this task.

## Next milestone: M3 — Ideal QAOA

Reuse these exact frozen M2 QUBOs on ideal simulators. Compare sampled QAOA
bitstrings, all optimum probabilities and decoded objectives against the saved
exact ground states before introducing any noise or hardware questions.
M3 is described here only. QAOA, circuits, IBM Runtime, warm starts, hybrid
solvers and hardware integrations are not implemented.
