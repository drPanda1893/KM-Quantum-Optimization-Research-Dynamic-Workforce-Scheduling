# M1 mathematical scheduling model

M1 is a fixed-slot workforce assignment problem. It establishes a classical
reference before any QUBO or quantum implementation. All instances in the
repository are synthetic. M1 makes no claim about quantum advantage.

## Units and domain

Time is a nonnegative integer number of minutes relative to an instance-local
origin. A job `j` occupies the half-open interval `[start_minute, end_minute)`;
its duration is `d_j = end_minute - start_minute > 0`. Endpoints that touch do
not overlap. Jobs cannot move, split, or change duration during optimization.

A worker `w` has a skill set `S_w`, explicit availability intervals `A_w`, a
nonnegative workload limit `M_w`, and an optional nonnegative integer
`cost_per_hour`. Cost rates use minor monetary units (for example, 2400 means
EUR 24.00/hour). The instance must use one consistent monetary unit; the solver
does not convert currencies. An omitted rate contributes zero cost.

A job has required skills `R_j`, a positive integer `priority` `p_j`, an
optional nonnegative `unassigned_penalty` override, and a `must_assign` flag
(default false). Requiring an empty skill set is permitted. Worker/job IDs must
be unique within their respective sets. Empty worker/job sets are supported.

Pydantic schemas reject invalid intervals, duplicate entity IDs, negative
capacities/rates/weights, noninteger minutes, and unknown fields. Availability
intervals may be unordered but cannot overlap. Adjacent availability intervals
remain separate: a job must fit completely inside one interval. For example,
`[0,30)` and `[30,60)` do not permit a job `[20,40)`; use `[0,60)` to represent
continuous availability. Skills serialize in sorted order.

## Variables and hard constraints

Let `W` be the workers and `J` the jobs. Binary `x_jw` means worker `w` executes
job `j`; binary `u_j` means job `j` remains unassigned. Define an eligible pair
by `R_j subseteq S_w` and full containment of the job inside one interval in
`A_w`. CP-SAT creates assignment variables only for eligible pairs; every
other `x_jw` is mathematically fixed to zero.

**C1 — Coverage**

```text
sum(w in W) x_jw + u_j = 1             for every job j
u_j = 0                              if j.must_assign is true
```

An optional job can be omitted with its objective penalty. A mandatory job
without an eligible assignment makes the instance infeasible. Optional jobs
remain explicit in the schedule, rather than disappearing silently.

**C2 — Skills:** `x_jw = 0` whenever a required skill is absent.

**C3 — Availability:** `x_jw = 0` unless one availability period fully contains
the job. Being available for only part of a job is insufficient.

**C4 — No overlap:** if `max(start_j,start_k) < min(end_j,end_k)`, then
`x_jw + x_kw <= 1` for every worker `w`. CP-SAT expresses this using an optional
fixed interval for each eligible pair and one `NoOverlap` per worker.

**C5 — Workload:** `L_w = sum(j in J) d_j*x_jw <= M_w`. This counts job minutes,
not elapsed span, idle time, overtime, or travel. CP-SAT gives each load an
integer domain bounded by the smaller of capacity and total job duration.

## Shared integer objective

`ObjectiveWeights` is the central configuration. All weights are nonnegative
integers. The defaults in `configs/baseline.json` are:

| Parameter | Default | Meaning |
| --- | ---: | --- |
| `unassigned_penalty` | 100000 | Base penalty if a job has no override |
| `cost_weight` | 0 | Weight of rounded worker costs |
| `imbalance_weight` | 0 | Penalty per minute of workload range |

For each job set `q_j` to its explicit `unassigned_penalty` if present,
otherwise to the configured default. Let `r_w` be the hourly minor-unit rate,
using zero when absent. Minimize:

```text
U = sum(j in J) q_j*p_j*u_j
C = cost_weight * sum(j in J, w in W) ceil(r_w*d_j/60)*x_jw
B = imbalance_weight * (max(w in W) L_w - min(w in W) L_w)
F = U + C + B
```

When there are no workers, `B = 0`; with one worker the range is zero. The
range includes every worker, including workers with zero load and workers
who cannot execute any job. Each assignment's cost rounds up separately to
one minor monetary unit, before multiplying by `cost_weight`. Integer
arithmetic implements the ceiling as `(rate * duration + 59) // 60`.

This is a weighted sum, not a lexicographic rule. The default penalty strongly
prefers assigning compatible jobs when the other terms are disabled. With
enabled costs/imbalance, a high enough weighted cost can deliberately favor
omission. A priority-3 omitted job costs three times the base penalty; an
explicit zero override remains zero. Use `must_assign` for a hard coverage
requirement rather than relying on a large finite penalty. Record all weights
for every experiment and do not tune them on the final test set.

The independent `evaluate_objective` function first validates a schedule and
then returns `total`, weighted `unassigned`, weighted `cost`, weighted
`imbalance`, and `worker_loads`. It ignores the schedule's claimed objective.
Both solvers independently validate and rescore every returned incumbent.
Benchmarks also revalidate/rescore and reject inconsistent reported objectives.

## Schedule validation and reference solvers

A `Schedule` names its `instance_id` and explicitly partitions every job into
one assignment or one entry in `unassigned_job_ids`. It also carries its
objective value and solver metadata when produced by a solver. The independent
validator accepts either a model or raw JSON, reparses its structure, and
reports structured violations with `code`, `message`, and optional IDs. Its
result includes `is_valid`, `violations`, and `violation_count`.

It checks instance identity, unknown workers/jobs, missing jobs, duplicate
assignments, duplicate unassigned IDs, jobs both assigned and unassigned,
mandatory coverage, skills, availability, overlap, and workload. Schema-invalid
candidates produce `invalid_structure`; they do not count as valid because
they originated from a solver. The evaluator refuses invalid schedules.

CP-SAT returns `OPTIMAL`, `FEASIBLE`, `INFEASIBLE`, `UNKNOWN`, or `MODEL_INVALID`
as applicable. Only an `OPTIMAL` schedule sets `proven_optimal=true`. A timeout
with an incumbent gives a feasible solution without proof; no incumbent gives
no schedule/objective. Proving infeasibility does not claim an optimal schedule.
Numeric guards reject instances whose expanded objective bounds or time/duration
values exceed `2**53 - 1`, to preserve exact comparison against OR-Tools' public
double-valued objective accessor and avoid internal integer overflow.

The exhaustive reference enumerates all `(len(W)+1)**len(J)` worker/unassigned
choices, with no CP-SAT preprocessing or model. It uses the public validator
for feasibility and the public evaluator for scoring. Complete enumeration
proves optimality or infeasibility. A state-count guard (default 1000000) rejects
oversized requests; a time limit can leave an incumbent unproven. This oracle
checks the CP-SAT formulation, but shares the validator/evaluator: dedicated
hand-expected tests independently check those two components. A further
test-only plain-Python oracle independently computes all five constraints and
all objective terms without production metrics helpers, and checks CP-SAT on
150 frozen development seeds. For exhaustive optima exceeding the exact double
range, the integer objective remains exact and the floating-point bound is
omitted (null); proof and zero gap are still reported.

Ten tiny cases document their manually derived objective, status, one optimum,
and explanation under `metadata.reference`. They cover unique skill assignments,
touching intervals, priority conflicts, capacity, impossible optional jobs,
availability, impossible mandatory jobs, multiple optima, worker cost, and
split availability. Equal optimal objectives are required across solvers;
identical assignments are not required when several optima exist.

## Scope limits

M1 does not implement dynamic schedule repair, schedule-change penalties,
floating job start times, travel/routing, overtime, breaks or labor legislation,
payroll, geographic data, customer datasets, a product UI, QUBO/Ising, QAOA,
quantum dependencies, or IBM integration. It supports the documented fixed-slot
model only. M2 is QUBO mapping plus exhaustive equivalence verification against
this original feasibility and objective definition.
