# M2 implementation decision

M1 is frozen at `7f6fbcc881fb4070aad0d54230e63e8fc9f7c04c` on
`feat/m1-classical-baseline`. The required research documents and existing domain,
metrics, solvers, fixtures, CLI, benchmarking and tests were reviewed before M2
implementation. Work proceeds on `feat/m2-qubo-mapping`.

Implement a SDK-independent integer polynomial with deterministic ID-sorted
primary variables and bounded binary auxiliaries. Remove only skill-incompatible,
unavailable and individually over-capacity assignments. Record every removal.

Use squared coverage and mandatory-omission penalties, pairwise overlap terms,
and exact workload equalities with nonnegative slack. Divide each workload
inequality by its duration GCD and floor its capacity, preserving the inequality.
Omit inequalities that cannot bind for any assignment bit pattern.

Encode the existing maximum-minus-minimum imbalance through bounded integers
A, B, D and nonnegative slacks, with A >= each load, B <= each load, D=A-B.
Its objective is the original weight times the duration unit times D. These
are epigraph constraints: minimizing over their feasible extensions reproduces the exact
M1 range; arbitrary noncanonical auxiliary states may have higher business
energy. No M1 objective is replaced.

Business energy is nonnegative for every bit pattern. Derive a feasible-objective
upper bound B from per-job maximum feasible business charges and the workload
range bound. Integer hard penalties P>B exclude every violated encoded
constraint from a ground state whenever a feasible original schedule exists.
Scaling is rational, rounded up, and its guarantee is explicitly recorded.

All minima, including auxiliary degeneracy, are decoded without repairs and
independently checked by M1. A separate M2 schema extends research metadata
without changing the frozen M1 record format. Complete small-state spectra,
100 fixed-seed cases, unsafe-penalty counterexamples and exact scaled-integer
Ising conversion provide acceptance evidence.

Logical limitation: an infeasible original problem has no valid schedule, but
its unconstrained QUBO still has minima. Such instances report INFEASIBLE and
never claim the feasible-instance equivalence gate PASS. A time-limited M1
oracle without proof likewise cannot establish equivalence.

No new runtime dependencies, QAOA, circuits, hardware, warm starts, hybrid
solver, customer data or M3 implementation are introduced.
