# M2 exact QUBO and Ising formulation

M1 remains the fixed classical reference: integer minutes, fixed half-open job
intervals, separate availability periods, mandatory/optional coverage, rounded
assignment costs, and the workload range across **all** workers. Its validator
and objective evaluator are unchanged. M2 encodes that problem using exact
integer binary polynomials, without a quantum SDK or optimization algorithm.

## Canonical variables and preprocessing

Sort jobs by ID and workers by ID. Assignment variables are indexed in that
job/worker order; unassigned variables follow in job order; workload-slack and
imbalance groups follow deterministically. The leftmost serialized bit is index
zero. The mapping records every bit's index, name, kind, job/worker IDs, integer
group, and weight. Names use JSON-escaped IDs to prevent ambiguous concatenation.

An assignment `x_jw` exists only if all required skills are present, the job is
fully inside **one** availability period, and its duration does not exceed the
worker's workload cap. Each removal records its reason. Every omitted pair is
impossible in any feasible M1 schedule, so this removes no valid solution.
Adjacent availability periods stay separate, exactly as in M1. Every job has
an explicit `u_j`, including mandatory jobs; a separate penalty enforces
mandatory coverage. The mapping fingerprints the whole instance with entity
and availability ordering normalized; model/instance mismatches are rejected.

The JSON artifact stores all coefficients, the constant, components, mapping,
normalized configuration, and penalty derivation. Equivalent reordered input
entities yield the same encoding. The benchmark additionally hashes the actual
input snapshot. There is no timestamp in the encoded model.

## Polynomial representation and expansion

For bits `v_i`:

```text
Q(v) = c + sum_i a_i*v_i + sum_(i<k) b_ik*v_i*v_k
```

Coefficients and the constant are Python integers. Terms are sorted, unique,
nonzero, and in range. Combining terms uses `v_i**2 = v_i`, including diagonal
terms produced by squared equations. For an integer residual
`r = t + sum_i d_i*v_i`:

```text
P*r**2 = P*t**2
         + sum_i P*(d_i**2 + 2*t*d_i)*v_i
         + sum_(i<k) 2*P*d_i*d_k*v_i*v_k
```

The matrix representation is **upper triangular**: `Q_ii=a_i`, `Q_ik=b_ik`
for `i<k`, and zero below the diagonal. Thus `v.T Q v + c` evaluates the same
polynomial. Do not halve upper-triangular couplings. A symmetric alternative
would put `b_ik/2` in both positions; that is not this artifact's convention.

## Business objective

Retain M1's omission penalty `q_j*p_j`, including an explicit zero override,
and assignment cost `c_jw = cost_weight*ceil(rate_w*duration_j/60)` with the
same per-assignment integer rounding:

```text
business(v) = sum_j q_j*p_j*u_j + sum_jw c_jw*x_jw
              + imbalance_weight*g*D
```

The last term is present only when the imbalance encoding is active. `g` is
the global GCD of eligible job durations (or one if no eligible pair), and `D`
represents the normalized workload range using bounded integer bits.

The energy breakdown reports `business`, `coverage`, `mandatory`, `overlap`,
`workload`, and `imbalance_constraints`, with their sum as `total`. The first
is a weighted business cost; the others are constraint penalties. No component
is inferred from the decoder, and none silently repairs a candidate.

## Coverage, mandatory jobs and overlap

Let `s_j = sum_w x_jw + u_j`. Add:

```text
coverage = P * sum_j (s_j - 1)**2
mandatory = P * sum_(j.must_assign) u_j
overlap = P * sum_(w, unordered overlapping eligible job pairs j,k) x_jw*x_kw
```

Coverage expands to `P*(1 - sum_i v_i + 2*sum_(i<k) v_i*v_k)` within each job's
assignment/unassigned group. Exactly one selected variable gives zero; zero
or multiple selections are penalized. Mandatory `u_j=1` is also penalized.
Overlap is zero for 00, 01, and 10, and equals P for 11. Each unordered pair is
encoded once. Half-open jobs touching at an endpoint do not conflict. Skills,
availability, and individual-capacity incompatibility need no penalties after
the documented pruning.

## Exact workload inequalities with bounded binary slack

For worker `w`, let `g_w` be the GCD of its eligible durations and let
`C_w=floor(max_work_minutes_w/g_w)`. Every possible load is a multiple of `g_w`,
so dividing durations by `g_w` and flooring the capacity preserves the integer
inequality exactly. If the sum of all eligible durations is within capacity,
the constraint is redundant and no slack is needed. Otherwise encode:

```text
sum_j (duration_j/g_w)*x_jw + S_w - C_w = 0
workload_w = P * (that residual)**2
0 <= S_w <= C_w
```

Represent an integer `S in [0,C]` by bounded binary weights. For `C>0`, use
`k=bit_length(C)=ceil(log2(C+1))` bits with weights
`[1,2,...,2**(k-2), C-(2**(k-1)-1)]`. At `C=0` use zero bits. For example:

| Bound | Weights | Representable values |
| ---: | --- | --- |
| 0 | `[]` | 0 |
| 1 | `[1]` | 0–1 |
| 2 | `[1,1]` | 0–2 |
| 5 | `[1,2,2]` | 0–5 |
| 7 | `[1,2,4]` | 0–7 |

The weights sum to the exact bound and represent every integer in the range.
Some values have multiple encodings. This creates real auxiliary ground-state
degeneracy, which the solver retains and checks. It does not create out-of-range
slack states. The equation has a zero-residual slack extension exactly when the
selected workload is within capacity; no approximation replaces the inequality.
GCD normalization reduces bit counts without changing time or cost semantics.

## Exact workload-range objective via a lower envelope

When imbalance is weighted, there are at least two workers and some possible positive load,
normalize all loads by global GCD `g` to integers `l_w`. Let
`H_w=min(floor(capacity_w/g), sum of normalized eligible durations)` and
`H=max_w H_w`. Use bounded integer groups:

```text
0 <= A <= H                 maximum epigraph
0 <= B <= min_w H_w         minimum hypograph
0 <= D <= H                 range
0 <= R_upper,w <= H
0 <= R_lower,w <= H_w       when min_w H_w > 0
```

Add squared residuals:

```text
A - l_w - R_upper,w = 0     for every worker
l_w - B - R_lower,w = 0     when min_w H_w > 0
A - B - D = 0
```

The upper equations impose `A>=max_w l_w`; the lower equations impose
`B<=min_w l_w`. If the minimum upper bound is zero, B is fixed to zero and
the lower equations are redundant. The business term minimizes
`imbalance_weight*g*D`. For each valid schedule a canonical extension chooses
`A=max l_w`, `B=min l_w`, and `D=A-B`; its energy equals M1's range exactly.
Every zero-residual extension has `D>=max l_w-min l_w`.

This is **lower-envelope equivalence**, not pointwise equality for every choice
of auxiliary bits. Noncanonical zero-penalty choices can inflate the encoded
business energy for the same valid schedule. Minimizing over zero-residual
auxiliary extensions recovers its exact M1 objective. Safe penalties also make
this the unrestricted auxiliary minimum. Under safe penalties a global minimum cannot
use an inflated extension, since its canonical extension is strictly cheaper.
The verification records noncanonical and auxiliary-invalid states separately.
It checks canonical extension energy for **every valid primary schedule**, as
well as checking every ground state. Workers with zero load remain included.

## Derived safe penalties and sensitivity

All business terms are nonnegative for every bitstring. A bound on the business
objective of every feasible schedule is:

```text
B_upper = sum_j max(allowed omission cost, max eligible assignment cost)
          + imbalance_weight*g*H
```

For mandatory jobs the allowed omission cost in this bound is zero; for jobs
without eligible assignments the max still exists using that entry. The
imbalance term is zero when no imbalance encoding is required. This bound is
conservative and derives from the instance/configuration, never a test optimum.
It does not claim that a mandatory-infeasible instance has a feasible solution.

Every nonzero integer squared residual, selected mandatory omission, or selected
conflicting pair contributes at least P. Choose
`P_safe=B_upper+penalty_margin`, with a positive integer margin (default one).
Then any encoded invalid state has `Q>=P_safe>B_upper`, while every feasible
schedule has a canonical extension with `Q=F<=B_upper`. Consequently all global
minima are zero-penalty encodings, decode to feasible M1 schedules, and attain
the original optimum. The strict inequality excludes invalid ties. If M1 is
infeasible, a QUBO still has a minimum, but that minimum cannot certify a feasible
schedule: report **INFEASIBLE**, never equivalence PASS.

Research scaling is explicit:
`P_applied=ceil(Fraction(P_safe)*Fraction(penalty_scale))`. Rational scale strings
such as `"1/4"`, `"1/2"`, `"1"`, `"2"`, and `"4"` normalize exactly. Metadata
records bounds, margin, safe/applied values, and
`safety_guaranteed=(P_applied>B_upper)`. An unsafe scale need not fail on every
instance; it loses the proof. A dedicated sensitivity example demonstrates
actual invalid ground states below the safe threshold. Those expected failures
are reported separately and do not tune the default.

## Decoder and exhaustive diagnostics

The decoder reads assignment and unassigned bits literally. The separate
`auxiliary_values(mapping, bits)` helper exposes the encoded integer values;
they are not included in the decoded schedule's metadata. The decoder does not
infer missing unassigned bits, discard duplicate assignments, or optimize/repair
a schedule. Malformed bit lengths or values and mismatched mapping identity are
errors. The unchanged M1 validator
then decides schedule validity; the unchanged evaluator scores valid schedules.

The exact QUBO solver enumerates all `2**n` states with integer Gray-code energy
updates, retains **all** equal minima, and independently analyzes every decoded
minimum. It also records spectrum levels, first excited energy/gap, decoded and
encoded feasibility counts, best valid and invalid energies, and the invalid
energy margin above ground.
A schedule may be valid while its slack/auxiliary bits violate an equation:
this is an invalid **encoding**, and the best-invalid metric includes it.
An explicit state cap rejects excessive requests before enumeration; no
truncated search is reported as proof.

## Exact Ising substitution

Substitute `x_i=(1-z_i)/2`, with `z_i in {-1,+1}` and bit 0 corresponding to
spin +1. From the polynomial coefficients:

```text
H(z) = c_I + sum_i h_i*z_i + sum_(i<k) J_ik*z_i*z_k
4*c_I = 4*c + 2*sum_i a_i + sum_(i<k) b_ik
4*h_i = -2*a_i - sum_(k adjacent to i) b_ik
4*J_ik = b_ik
```

`IsingModel` serializes `constant4`, `fields`, `couplings`, and
`energy_denominator=4`, all integer coefficients. Its physical `energy(spins)`
returns `Fraction(energy4,4)`. No floating tolerance, omitted offset, SDK endian
convention, Hamiltonian normalization, or hardware scaling enters this mapping.
Verification compares exact energies on **all** states, not only ground states.
The [worked mini example](m2_mini_example.md) shows the complete transformation.

## Growth and limitations

Assignment count is at most `workers*jobs`; every job adds one unassigned bit.
Each active workload group adds `ceil(log2(C_w+1))` bounded slack bits. Active
imbalance adds A/B/D plus up to two slack groups per worker. Squared residuals
couple all bits in each equation, so sparse assignments can still produce dense
connectivity. Record assignment/unassigned/slack/auxiliary counts, nonzero
couplings, pair density, pruning counts/ratio, and coefficient range. The
coefficient dynamic range is an exact rational string and excludes the offset.

Exhaustive verification is exponential and restricted to tiny instances. Large
safe penalties and duration-weighted squares can produce a wide coefficient
range; M2 establishes mathematical equivalence, not hardware suitability.
There is no QAOA, circuit, QPU, noise experiment, warm start, hybrid solver, or
quantum-advantage claim. M3 is only a future ideal-QAOA study using these frozen
artifacts and exact reference ground states.
