# Research Plan

## KM³ Quantum Optimization Research — Dynamic Workforce Scheduling

**Version:** M0.1  
**Date:** 2026-10-07  
**Status:** Research contract / draft baseline  
**Project type:** Applied optimization research with a potential KM³ product path

---

## 1. Research objective

The project investigates whether gate-based quantum optimization or hybrid quantum-classical methods can provide measurable value for **dynamic workforce scheduling**, compared with strong classical optimization methods.

The project explicitly does **not** assume that quantum computing is superior.

Primary research question:

> Under which problem structures, problem sizes, disruption scenarios and compute budgets can quantum or hybrid quantum-classical optimization match or improve strong classical scheduling methods?

Separate business question:

> Can KM³ build a practical optimization engine that improves real operational planning, independent of whether the winning solver is classical, quantum or hybrid?

This separation is intentional. The research remains meaningful if quantum methods do not outperform classical methods, while the product idea can still be commercially useful.

---

## 2. Initial real-world use case

The first target is **dynamic field-service workforce scheduling**.

A company has workers with different skills and working hours. Customer jobs require specific skills, occur in specific time windows, have different priorities and may overlap. During the day, conditions change: a worker becomes unavailable, a job takes longer than expected, or an urgent job is inserted.

The scheduler must create or repair a plan that is feasible and economically useful.

### Worker

- pseudonymous worker ID
- skill set
- availability
- maximum working time
- optional cost class

### Job

- pseudonymous job ID
- required skills
- duration
- allowed time slot / time window
- priority
- optional location
- optional service-level deadline

### Dynamic event

- worker unavailable
- urgent job added
- job cancelled
- duration changed
- time window changed

---

## 3. Scope discipline

### Included first

- discrete workforce assignment
- skills
- availability
- fixed or discretized job time slots
- overlapping-job constraints
- workload limits
- priorities
- dynamic schedule repair
- synthetic benchmark generation
- exact / classical / quantum / hybrid comparison

### Deliberately excluded from M1

- full vehicle routing
- continuous geographic routing
- complex labor-law modeling
- payroll
- production deployment
- SaaS UI
- LLM-generated constraints
- autonomous customer decisions

Travel and routing can be added later as a separate optimization layer. A time-indexed QUBO grows very quickly, so the first scientific question must remain testable before adding every real-world feature.

---

## 4. Research questions

### RQ1 — Classical baseline

How effectively can a strong classical CP-SAT model solve the defined workforce scheduling instances in terms of feasibility, objective quality and runtime?

### RQ2 — QUBO representation

How does the transformation from the constrained scheduling problem into QUBO / Ising form affect variable count, penalty sensitivity, feasibility, objective fidelity and solution quality?

### RQ3 — Standard QAOA

How does standard QAOA perform against the exact optimum and CP-SAT on small instances under equal, explicitly reported evaluation budgets?

### RQ4 — Warm-start QAOA

Does a classical warm start improve QAOA convergence, feasibility or solution quality compared with standard QAOA?

### RQ5 — Noise and real hardware

How much performance is lost when moving from ideal simulation to shot-based simulation, noisy simulation and finally real QPU execution?

### RQ6 — Hybrid decomposition

Can classical decomposition reduce a larger scheduling problem into quantum-sized subproblems without destroying global solution quality?

### RQ7 — Dynamic replanning

Can hybrid optimization repair a disrupted schedule while minimizing operational cost and unnecessary changes to the existing plan?

---

## 5. Hypotheses

The hypotheses are intentionally falsifiable.

- **H1:** CP-SAT will dominate small and medium practical instances in wall-clock time and optimality during the initial project stages.
- **H2:** Standard QAOA will become difficult to scale because workforce-assignment encodings create many binary variables and constraint penalties.
- **H3:** Warm-start QAOA will produce better median solution quality and/or convergence behavior than randomly initialized standard QAOA under the same quantum evaluation budget.
- **H4:** Noise will materially reduce feasibility rate and optimal-solution probability compared with ideal simulation.
- **H5:** A hybrid decomposition approach can solve larger instances than a monolithic QAOA formulation while retaining useful solution quality.
- **H6:** Adding a schedule-stability term during replanning will reduce unnecessary reassignment after disruptions without unacceptable degradation of the primary business objective.

Any exploratory findings discovered after benchmark execution must be labeled exploratory rather than retroactively changing these hypotheses.

---

## 6. Formal optimization model

### 6.1 M1 model: fixed-slot assignment

The initial formulation intentionally uses jobs with known or discretized time slots.

Let:

- J = set of jobs
- W = set of workers
- x(j,w) in {0,1} = 1 if worker w executes job j

Hard constraints include:

1. **Job coverage:** each required job is assigned to exactly one compatible worker, or to an explicit unassigned variable where infeasibility is permitted.
2. **Skill compatibility:** a worker may only receive a job if mandatory skill requirements are satisfied.
3. **Availability:** a worker may only receive jobs during available periods.
4. **Conflict prevention:** a worker may not execute overlapping jobs.
5. **Workload:** capacity / working-time limits must be respected.

### 6.2 Objective

The business objective is represented as a weighted or lexicographic combination:

C =
lambda_1 * C_unassigned
+ lambda_2 * C_priority
+ lambda_3 * C_overtime
+ lambda_4 * C_imbalance
+ lambda_5 * C_change
+ lambda_6 * C_travel

Not every term is active in M1.

For dynamic replanning, C_change measures the distance between the new assignment and the previous schedule. This explicitly penalizes changes that are not operationally necessary.

---

## 7. QUBO / Ising formulation

The quantum track transforms the binary scheduling problem into a Quadratic Unconstrained Binary Optimization problem:

minimize x^T Q x + c^T x + k

Hard constraints are represented through penalty terms:

H =
H_business
+ P1 * H_coverage
+ P2 * H_conflicts
+ P3 * H_capacity

The QUBO is then translated to an Ising cost Hamiltonian.

### Critical research variable: penalty selection

Penalty weights must not be hand-tuned on the final test set.

The project will compare:

- mathematically derived safe penalties where available
- normalized heuristic penalties
- sensitivity sweeps on a development set

The final test set remains untouched until configurations are frozen.

---

## 8. Solvers

### Exact / reference

For sufficiently small instances:

- exhaustive enumeration where practical
- CP-SAT with proven optimal status

These provide ground truth.

### Classical baseline

Primary baseline:

- Google OR-Tools CP-SAT

Potential secondary baselines:

- greedy heuristic
- local search
- simulated annealing
- MILP where useful

Weak baselines must never be used alone to justify a quantum advantage claim.

### Quantum sequence

1. exact statevector QAOA
2. shot-based QAOA
3. noisy QAOA
4. real-QPU QAOA
5. warm-start QAOA
6. advanced variants only after the baseline is stable

### Hybrid candidate

1. classical preprocessing removes impossible assignments
2. CP / relaxation identifies promising regions
3. the problem is decomposed into smaller binary subproblems
4. QAOA explores selected subproblems
5. classical repair enforces feasibility
6. a global classical evaluator scores the final schedule

All classical work used by the hybrid method must be counted in end-to-end evaluation.

---

## 9. Benchmark instance families

Synthetic instances are generated from explicit seeds.

Dimensions:

- worker count
- job count
- skill density
- worker-job compatibility ratio
- temporal overlap density
- workload tightness
- priority distribution
- disruption type
- disruption severity

Initial scale tiers:

| Tier | Example size | Purpose |
|---|---:|---|
| T0 | 3 workers / 4 jobs | mathematical validation |
| T1 | 4 workers / 5 jobs | small QAOA / exact comparison |
| T2 | 6 workers / 8 jobs | simulator scaling |
| T3 | 10 workers / 15 jobs | classical + decomposition |
| T4 | 25 workers / 40 jobs | realistic classical/hybrid test |
| T5 | pilot-derived | later real-world validation |

The exact number of QUBO variables is reported for every instance because worker/job counts alone hide encoding growth.

---

## 10. Metrics

### Solution metrics

- feasible / infeasible
- objective value
- optimality gap or best-known gap
- percentage of jobs assigned
- hard-constraint violations
- soft-constraint penalties
- schedule disruption distance

### Runtime metrics

- preprocessing time
- optimizer time
- total local wall-clock time
- QPU execution time
- queue time reported separately
- postprocessing / repair time

### Quantum metrics

- logical problem variables
- physical qubits used
- circuit depth
- two-qubit gate count
- shots
- QAOA depth p
- optimizer evaluations
- probability of optimal solution where ground truth exists
- probability of feasible solution
- approximation ratio where meaningful
- hardware/backend identifier
- calibration / execution timestamp where available

### Cost metrics

Where available:

- QPU usage / credits
- estimated compute cost

---

## 11. Experimental protocol

Every experiment stores:

- git commit SHA
- dependency lock
- instance seed
- instance JSON
- solver configuration
- random seed
- backend information
- raw result
- derived metrics

Synthetic instance generators use separate development, validation and final test instances. Penalty weights and hyperparameters are not tuned on the final test set.

Stochastic simulator runs use repeated independent seeds. The exact repetition count is frozen before final benchmarking. Real-QPU runs may use fewer repetitions because access is limited; this limitation must be stated rather than hidden.

Report distributions, not only the best run. At minimum report mean, median, spread, feasibility/success rate and number of repetitions.

No cherry-picking of favorable quantum runs.

---

## 12. Fair comparison rules

A claim such as "quantum is better" requires an explicitly defined comparison dimension, for example:

- solution quality under a fixed evaluation budget
- probability of reaching a target solution
- solution quality under a fixed runtime budget
- scaling behavior
- robustness to disruption

Because QPU queue time is an infrastructure artifact, results report both algorithm/execution time and end-to-end elapsed time.

The project must distinguish:

- quantum advantage
- quantum-inspired improvement
- hybrid improvement
- useful business optimization

These are not interchangeable claims.

---

## 13. Dynamic replanning experiment

After a baseline schedule has been created, inject one disruption:

- one worker becomes unavailable
- one urgent job is inserted
- one job duration increases

Compare:

### A. Full reoptimization

Optimize from scratch.

### B. Stability-aware reoptimization

Optimize with an additional change penalty.

Evaluate:

- business objective after disruption
- number of changed assignments
- number of workers affected
- runtime
- feasibility

This matters because a mathematically optimal plan can be operationally poor if every small disruption reshuffles the whole day.

---

## 14. Milestones

### M0 — Research contract

Deliverables:

- research plan
- research questions
- hypotheses
- experiment rules
- initial repository structure

Exit criterion: scope and metrics are frozen enough to implement without redefining success after seeing results.

### M1 — Classical scheduling baseline

Deliverables:

- domain model
- synthetic instance generator
- independent schedule validator
- objective evaluator
- CP-SAT formulation
- unit tests
- tiny hand-verifiable reference cases

Exit criterion: deterministic instances can be solved reproducibly and feasibility/objective calculations are independently verified.

### M2 — QUBO mapping

Deliverables:

- binary/QUBO formulation
- Ising translation
- penalty strategy
- equivalence tests against the original objective

Exit criterion: for small exhaustive instances, the QUBO ground state corresponds to a valid optimum of the original model.

### M3 — Ideal QAOA

Deliverables:

- statevector implementation
- shot-based simulator
- benchmark harness
- parameter / depth experiments

Exit criterion: repeated results are reproducible and compared with exact/CP-SAT ground truth.

### M4 — Noise + warm start

Deliverables:

- noisy simulation
- standard vs warm-start QAOA
- penalty sensitivity analysis
- circuit metrics

Exit criterion: H3 and H4 can be evaluated on predeclared benchmark families.

### M5 — Real QPU

Deliverables:

- selected small test set
- QPU execution
- hardware metadata
- simulator-to-hardware comparison

Exit criterion: hardware results support bounded claims with explicit uncertainty.

### M6 — Hybrid dynamic scheduler

Deliverables:

- classical preprocessing
- decomposition
- quantum subproblem solver
- classical repair
- disruption/replanning benchmark

Exit criterion: end-to-end hybrid performance can be compared fairly against CP-SAT.

### M7 — Pilot translation

Only after the benchmark framework is stable:

- define one real customer workflow
- derive a privacy-preserving instance schema
- capture the customer's current planning baseline
- compare business KPIs

No employee names or unnecessary personal data enter the public research repository.

### M8 — Scientific report + product decision

Possible outcomes:

1. **Classical wins** — build KM³ Optimizer classically; publish the quantum benchmark.
2. **Hybrid helps in a narrow regime** — use hybrid only for that regime.
3. **Quantum signal is promising but immature** — keep R&D separate from production.
4. **No useful business optimization** — stop productization and preserve the research result.

---

## 15. Proposed repository structure

~~~text
.
├── README.md
├── RESEARCH_PLAN.md
├── pyproject.toml
├── uv.lock
├── docs/
│   ├── model.md
│   ├── experiment_protocol.md
│   └── decisions/
├── src/
│   └── km_quantum/
│       ├── domain/
│       ├── instances/
│       ├── solvers/
│       │   ├── classical/
│       │   ├── quantum/
│       │   └── hybrid/
│       ├── qubo/
│       ├── experiments/
│       └── metrics/
├── tests/
├── configs/
├── data/
│   └── synthetic/
├── results/
│   ├── metadata/
│   └── summaries/
└── notebooks/
~~~

Notebooks are for exploration only. Reproducible benchmark logic belongs in source code and CLI/script entry points.

---

## 16. Initial implementation stack

Prefer stable current packages, then pin exact versions:

- Python 3.11 or 3.12
- OR-Tools
- Qiskit SDK 2.x
- Qiskit IBM Runtime
- Qiskit Aer
- Qiskit Optimization Mapper addon
- NumPy
- SciPy
- pandas
- pydantic
- pytest
- matplotlib

IBM's current QAOA and warm-start documentation uses Qiskit SDK 2.x and current Qiskit Runtime APIs. Exact versions must be captured in the dependency lock when implementation begins.

---

## 17. Data protection

Initial research uses synthetic data only.

For a later pilot:

- use pseudonymous IDs
- collect only optimization-relevant attributes
- exclude names, contact data and unrelated performance data
- document the purpose of each field
- keep customer data outside the public repository
- define deletion and retention rules
- prefer aggregated/coarsened locations where exact locations are unnecessary

The optimizer must not become an employee-surveillance system.

---

## 18. Scientific claim policy

KM³ should not claim "quantum advantage" merely because:

- QAOA beats random assignment
- QAOA beats a deliberately weak heuristic
- one favorable run beats a classical median
- simulator results look better than hardware results
- classical preprocessing did most of the work
- only tiny toy instances were evaluated

A strong claim requires a strong baseline, matched comparison, repeated experiments, held-out instances, reproducible code, transparent negative results and explicit uncertainty.

---

## 19. Go / no-go criteria

### Continue the scientific project if

the experiment framework remains reproducible and the research questions can be answered — even when results are negative.

### Continue the quantum product track only if

a quantum or hybrid method shows a reproducible advantage on at least one practically relevant metric against a strong classical baseline, with all classical preprocessing counted.

### Continue the optimization product track if

real or pilot-derived scheduling data show meaningful operational improvement compared with the customer's current planning process, regardless of which solver wins.

This prevents the product roadmap from depending on a premature quantum-computing claim.

---

## 20. First implementation task

The first code milestone should **not** be QAOA.

Build and verify:

1. immutable domain schema for workers, jobs and time slots
2. deterministic synthetic instance generator
3. independent schedule validator
4. objective evaluator
5. CP-SAT baseline
6. 5–10 tiny hand-verifiable instances
7. benchmark result schema

Only after those are stable should the same instances be mapped to QUBO.

This gives the project an oracle and prevents quantum experiments from optimizing the wrong mathematical problem.

---

## 21. References / starting points

### Official tooling

- IBM Quantum — QAOA tutorial: https://quantum.cloud.ibm.com/docs/en/tutorials/quantum-approximate-optimization-algorithm
- IBM Quantum — Warm-start QAOA: https://quantum.cloud.ibm.com/docs/en/tutorials/warm-start-qaoa
- IBM Quantum — Advanced QAOA: https://quantum.cloud.ibm.com/docs/en/tutorials/advanced-techniques-for-qaoa
- Google OR-Tools — Employee Scheduling: https://developers.google.com/optimization/scheduling/employee_scheduling
- Google OR-Tools — Constraint Optimization: https://developers.google.com/optimization/cp

### Research direction

- Application of QAOA to Job Shop Scheduling, European Journal of Operational Research: https://doi.org/10.1016/j.ejor.2023.03.011
- On the Baltimore Light RailLink into the quantum future — scheduling on quantum hardware: https://www.nature.com/articles/s41598-025-15545-0
- Path Matters: Industrial Data Meet Quantum Optimization — industrial QUBO transformations and solver/hardware sensitivity: https://arxiv.org/abs/2504.16607

---

## 22. M0 conclusion

The first goal is **not to prove quantum superiority**.

It is to build a reproducible experimental system that can answer:

> Where, if anywhere, does quantum or hybrid optimization add value to a real scheduling problem?

If the answer is "nowhere yet", that is useful evidence.

If a narrow advantage appears, the project will have the benchmark infrastructure required to test whether it survives larger instances, noise and real operational constraints.
