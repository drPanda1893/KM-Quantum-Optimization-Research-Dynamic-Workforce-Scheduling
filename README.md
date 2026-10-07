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

**M0 — Research contract / planning**

See RESEARCH_PLAN.md.

## Planned stack

- Python
- Google OR-Tools / CP-SAT
- Qiskit 2.x
- Qiskit Aer
- Qiskit IBM Runtime
- Qiskit Optimization Mapper addon
- NumPy / SciPy / pandas
- pytest

Exact versions will be pinned in the reproducible environment once implementation starts.

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
