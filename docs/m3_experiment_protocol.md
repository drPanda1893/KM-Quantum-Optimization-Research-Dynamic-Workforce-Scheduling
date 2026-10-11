# M3 ideal standard-QAOA experiment protocol

M2 established exact encoding equivalence. M3 measures whether ideal standard
QAOA concentrates probability on these correct scheduling solutions. Acceptance
depends on mathematical and implementation correctness, never positive quantum
performance. CP-SAT remains the business-objective oracle; exhaustive M2 ground
states remain the quantum-energy oracle. Statevector simulation runtime is not
a speed comparison against CP-SAT.

## Frozen inputs and gate order

The reference commit is `f489b365ebb1bf814c3087bd80969045a7817fa7`.
Before M3 source changes, SHA-256 was recorded for protected domain, metrics,
classical solvers, M2 implementation, tests, documents and fixtures in
`data/synthetic/m3/provenance.json`. M3 dependency declarations, CLI dispatch and
README changes are separately identified. The ignored M2 acceptance report was
absent in the recovered checkout and was reconstructed with unchanged M2 code;
its resulting bytes and PASS outcome are recorded separately in provenance.
Reconstruction is not presented as the original historical report.
The reconstructed report is versioned explicitly at `results/m2-acceptance.json`
so its exact provenance bytes remain available in subsequent checkouts.
The initial raw file hashes remain unchanged. Additional hashes normalize only
CRLF to LF, permitting Git line-ending conversions across platforms while
rejecting every other content change. The provenance result distinguishes
exact protected bytes from line-ending-only differences. Manifest source hashes
continue to identify the original raw snapshot. The reconstructed acceptance
report uses Git's `-text` attribute so its exact report bytes are preserved.

First verify protected hashes and the preserved reconstructed M2 acceptance gate. Next,
translate the frozen Ising representation and verify all computational-basis
energies of every eligible reference, including invalid states. A failed
operator/ordering gate blocks any QAOA quality experiment. Quantum correctness
fixtures establish the Z sign, ZZ coupling, uniform initial state, actual
parameter order and complete scheduling analysis. Evaluation begins only after
this gate passes and this configuration and manifest are frozen.

## Structural benchmark selection

`data/synthetic/m3/benchmark_manifest.json` is generated before observing QAOA
outcomes. It records the original reference file SHA-256, each canonical record
hash, QUBO and Ising hashes, source line, qubit count, all-ground-state count,
ground energy, invalid-encoding margin and M2 structure metrics.

The conservative cap is 14 binary variables. A complex128 vector at that cap
uses 262,144 bytes for amplitudes alone; simulator work and surrounding Python
objects add overhead. The primary suite is all 13 hand cases within the cap,
plus one random reference at each available qubit count: 1, 2, 3, 4, 5, 6, 7,
8 and 10. Within each random-size group, choose the minimum SHA-256 of the full
instance ID. This fixes 22 references and 660 primary runs. The sole larger
reference, hand case O at 15 bits, is explicitly excluded. The remaining random
references remain visible in the manifest and participate in correctness
verification: all 113 eligible references are audited, not just the quality
sample. The compact primary suite controls optimizer cost before outcomes are
known; it is not a random representative sample of practical scheduling.

For each instance, `int(SHA-256(instance_id),16) % 5 == 0` gives the development
split; all other residues give evaluation. This yields 3 development and 19
evaluation primary instances. Development may support technical debugging;
evaluation results do not justify changing budgets, tolerances, seeds or
Hamiltonian scaling. The M2 fixtures already supported mathematical development:
this deterministic split is a QAOA protocol separation, not a claim that these
synthetic cases are new unseen scheduling data.

## Primary configuration

Use Qiskit SDK 2.5.2 and local V2 `StatevectorEstimator` and
`StatevectorSampler`, NumPy, SciPy `minimize` and the Qiskit QAOA ansatz.
Store exact Python, platform, package, dependency-lock and Git identities with
every run. No account, credential, runtime service or remote quantum job is used.

| Setting | Prespecified value |
| --- | --- |
| Depths | `p = 1, 2, 3` |
| Restart initialization seeds | `0,1,2,3,4,5,6,7,8,9` per instance/depth |
| Initial angles | local `numpy.default_rng(seed).uniform(0, pi, actual_parameter_count)` |
| Optimizer | SciPy COBYLA |
| `maxiter` | 300 objective evaluations |
| `tol` | `1e-6` |
| `rhobeg` | `0.5` |
| Hamiltonian normalization | none; frozen M2 coefficients are used directly |
| Shot count after exact training | 1024 |
| Sampler seed | `10000 + initialization_seed + 100*p` |
| Energy verification tolerance | `atol=1e-9`, `rtol=1e-12` |
| Descriptive probability success criterion | strictly `P_ground > 0.5` |

Parameters are read from actual Qiskit Parameter objects. Bindings and saved
records map actual names and positions to values; neither beta/gamma order nor
their grouping is assumed. The state begins at `|+>^n` and the mixer is
`sum_i X_i`. No classical solution enters the initial state. Omit only the
constant Ising offset from the circuit Hamiltonian and restore it for every
energy report. No coefficient rescaling, penalty change, alternate objective,
warm start, custom mixer or optimizer search is allowed in the primary study.
The parameter count is normally `2*p`. A zero-qubit or constant Hamiltonian has
no effective parameters and uses its exact uniform distribution directly.

## Ordering, state analysis and energies

M2 bit index zero is the leftmost serialized character; its index i maps to
Qiskit qubit i. Qiskit displayed strings have qubit zero on the right and a
basis integer is `sum_i bit_i*2**i`. One explicit translation layer converts
these representations for basis construction, statevector probabilities and
measurement decoding. Reversals must not be scattered through analysis code.

After optimization, enumerate every final basis probability and reuse M2
energies, literal decoder and unchanged M1 validator/objective. Distinguish:

- `P_ground`: mass on every frozen ground bitstring, including degeneracy.
- `P_valid`: mass on M1-valid decoded schedules, irrespective of auxiliary bits.
- `P_encoded_valid`: mass on M1-valid schedules with zero encoded constraints.
- `decoded_optimal_schedule_probability`: mass on M1-optimal decoded schedules,
  irrespective of auxiliaries.
- `optimal_valid_probability`: mass on zero-penalty M1-optimal encodings whose
  encoded business energy equals the independently evaluated M1 objective.

With auxiliary encodings, decoded-optimum mass can exceed `P_ground`: a correct
optimal schedule can carry incorrect slack or an inflated noncanonical
imbalance extension. Report the extra mass explicitly. Verify containment of
the ground states in decoded optima, and equality of `optimal_valid_probability`
and `P_ground`. This preserves the frozen M1/M2 meanings instead of redefining
feasibility or treating bad auxiliary bits as a bad business schedule. The
energy-consistent meaning of `optimal_valid_probability` is explicit; it is not
presented as the total probability of decoding an optimal business schedule.

Independently compare final estimator energy including offset with
`sum_b P(b)*E_M2_Ising(b)`. Report raw initial/final energy, ground/max energy
and residual. Normalized error is `(E-E_ground)/(E_max-E_ground)`; a flat
spectrum is explicitly handled. Do not use an energy quotient whose sign or
offset can reverse its interpretation. Uniform ground mass is `g/2**n` and
probability lift is the final ground mass divided by that baseline.

## Traces, sampling and completeness

Write every optimizer evaluation to JSONL immediately, with index, ordered
parameters, offset-free expectation, total expected energy, elapsed time and
evaluation runtime. Preserve initial and final angles, optimizer nfev, success,
termination message and runtime. An exhausted evaluation budget remains a
reported run and contributes to the distributions; convergence is separately
counted. A technical failure is explicit, not silently replaced by another seed.

Run-level `runtime_ms` measures the core `run_qaoa` call: its reference/gate
checks when not already cached, circuit construction, exact optimization and
final state analysis, including writing optimization traces. It excludes the
separate shot-sampling call, run/sample artifact writing, suite-wide gates and
plot/summary generation. It is not total benchmark wall time. Time and timestamp
values are observational metadata and are not expected to reproduce bytewise.

Sampling occurs only after exact statevector optimization. Save all distinct
measurement counts, modal string/frequency, best sampled energy, all-ground
counts, valid/invalid counts, best decoded M1 objective and whether any optimum
was observed. Compare ground frequencies against the exact
distribution using a stated statistical test, not exact equality. Empirical
feasibility is reported alongside the exact decoded-valid mass; the formal
statistical audit tests the ground frequency. Ground-sample
verification uses the exact two-sided binomial test with the prespecified tail
threshold `alpha=1e-9`. Across 660 runs the union bound gives a false-rejection
probability at most `6.6e-7`. Report the p-value, absolute deviation, binomial
standard error and 95% confidence interval alongside empirical feasibility.

Primary outputs are JSON run/sample records, complete JSONL traces, restart
summary JSON, exploratory-correlation JSON and reproducible PNG charts under
`results/m3/`. Persist reproducibility metadata rather than large statevector
binaries. Store a clean code commit before reported primary execution; runtime
and timestamps are observational metadata, not expected to reproduce bytewise.

## Reporting and interpretation

Per instance/depth, report every restart's mean, median, sample standard
deviation, minimum and maximum energy/probabilities; median normalized energy
error; fraction above the prespecified probability criterion; optimizer
convergence; evaluations and runtime distributions. Never substitute the best
restart for the whole distribution. Development and evaluation summaries remain
identifiable; no after-the-fact sample removal is permitted.
Executing the development and evaluation splits separately filters only which
runs are newly executed. Shared summaries, correlations and plots are rebuilt
from all validated completed manifest runs already saved in the output directory,
so a later split invocation retains the earlier split's observations.

Create energy-vs-evaluation convergence, P_ground-vs-p, P_ground-vs-qubits,
P_valid-vs-qubits and QAOA-vs-uniform plots from saved raw data. Label averaging
or medians clearly. The depth comparison includes energy, probabilities,
runtime, evaluations and logical circuit counts; it does not assume that
larger p is better.

Exploratory Pearson associations use one restart-median per instance/depth,
stratified by split and p. Features include qubit count, invalid-encoding margin,
QUBO density, coefficient dynamic range, slack/assignment counts, preprocessing
reduction and active constraint component count. Outcomes include ground and
valid mass, normalized error and convergence fraction. Constant features or
fewer than three observations yield null correlations. This small structurally
selected synthetic suite has confounded features, repeated instances and weak
coverage: these descriptive associations carry no causal or confirmatory
interpretation. Do not invent retrospective hypotheses or claim quantum,
practical, business or runtime advantage from classical statevector simulation.

The completion report answers RQ-M3.1 through RQ-M3.6 from all saved results,
includes negative outcomes and exact tooling/test evidence, and verifies the
protected M2 hashes again. The next milestone is only **M4 — Noise + Warm-Start
QAOA**, to be implemented later; real QPU work remains M5.
