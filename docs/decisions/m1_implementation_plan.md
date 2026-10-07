# M1 implementation plan

The initial repository contains only README.md and RESEARCH_PLAN.md (M0).
Both documents were read in full before implementation. No existing code,
dependency lock, test conventions or architecture need migration.

Implement M1 on `feat/m1-classical-baseline` using Python 3.12, uv, Pydantic,
OR-Tools CP-SAT and pytest. Use the proposed src layout, with no quantum
dependencies or quantum implementation.

1. Define frozen, validated input schemas. Time is integer minutes and each job
   has a fixed half-open interval. Coverage is explicit per job (`must_assign`).
2. Define central integer objective weights: priority-weighted unassigned
   penalties, optional rounded worker costs, optional workload range penalty.
   Independently validate and score every returned schedule.
3. Build a CP-SAT baseline with explicit seed, worker count, wall-clock and
   optional deterministic-time budgets. Report bounds and proof status honestly.
4. Enumerate all choices on tiny instances as an independent reference. Compare
   objectives, including infeasibility and multiple optimum cases.
5. Generate synthetic instances deterministically from versioned parameters and
   seeds. Store ten hand-verifiable fixtures and test all hard constraints.
6. Provide JSON serialization, an argparse CLI and JSONL benchmark records that
   include configurations, instance identity, validation, versions and Git state.
7. Document the mathematical model and fair comparison protocol; install the
   locked environment, run tests, lint and type checking, and review the result.

Correctness and reproducibility take precedence over benchmark speed. M2 is
limited to a documented next step: QUBO mapping and equivalence verification.
