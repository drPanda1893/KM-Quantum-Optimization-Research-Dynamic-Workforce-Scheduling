# Independent M4 quality review

Source: saved 1200 density records and 36 controls. Python standard-library statistics only; no km_quantum reporting imports, optimization or density resimulation. All 240 instance/depth/algorithm/profile groups contain seeds 0..4. All observed code SHAs are 27f2b9e82fd76394f9860f455953eacc66b3cc42 with dirty=false.

Summary verification: 14,400 numeric fields agree; maximum absolute floating discrepancy 2.842170943040401e-14; zero mismatches at tolerance 1e-9.

## Equal-instance comparisons

Each value first takes a five-restart median within an instance. The mean below gives equal weight to the six instances in its group. Percentage points are absolute differences.

| Group | p | Profile | Mean difference of medians Warm−Std, pp | Median difference of medians, pp | Median of instance paired-seed-median differences, pp |
|---|---:|---|---:|---:|---:|
| known_diagnostic | 1 | ideal | 0.994972 | 0.930746 | 6.125423 |
| known_diagnostic | 1 | combined_high | 0.526478 | 0.824391 | 5.188745 |
| known_diagnostic | 2 | ideal | 11.323343 | 6.287949 | 1.779906 |
| known_diagnostic | 2 | combined_high | 7.984206 | 3.878859 | 0.397318 |
| new_evaluation | 1 | ideal | 6.673123 | 0.153404 | -0.471404 |
| new_evaluation | 1 | combined_high | 5.194382 | -0.127079 | -0.409850 |
| new_evaluation | 2 | ideal | 4.578653 | -0.382704 | 0.043154 |
| new_evaluation | 2 | combined_high | 3.573084 | -0.612750 | -0.585697 |

Positive new-suite means are dominated by new_20261101_simple: +42.2165 pp at p1 and +38.1762 pp at p2. New-suite median differences are near zero or negative. Sparse-skills is a trivial no-assignment case; both algorithms saturate near one, with ideal differences around 1e-8. These tiny differences have no substantive improvement interpretation.

## Classical product versus QAOA layers

| Group | p | Mean Warm−product, pp | Median Warm−product, pp | Mean Std−uniform, pp | Median Std−uniform, pp |
|---|---:|---:|---:|---:|---:|
| known_diagnostic | 1 | 8.046403 | 5.291188 | 14.984748 | 14.690634 |
| known_diagnostic | 2 | 21.235431 | 19.788988 | 17.845406 | 19.070828 |
| new_evaluation | 1 | 17.646286 | 15.628281 | 20.993750 | 13.901594 |
| new_evaluation | 2 | 18.687164 | 13.205322 | 24.129097 | 17.464222 |

Warm layers can lose application-optimal mass relative to the classical clipped product: M p1 −2.2465 pp; new_cost p1 −2.8239 pp; new_imbalance p1 −0.8639 pp and p2 −7.7039 pp. Deterministic rounding is valid for exactly A_single_job, new_simple and new_sparse_skills; the other nine rounded plans remain invalid with objective null.

## Exploratory K

Uniform schedule-optimal mass is 0.0625; warm product is 0.05885328353137. Ideal standard restart medians are 0.03399738745308 / 0.08107035122710 at p1/p2; warm medians are 0.09394627763941 / 0.09120764182649. Difference of medians improves by +5.9949 / +1.0137 pp, but p2 median paired-seed difference is −0.3656 pp. This illustrates why these statistics must be reported separately.

Ideal K ground medians are 0.00465347373366 / 0.00734683265461 (standard) and 0.01430843444386 / 0.01044826987373 (warm); all four are below uniform ground 0.015625. Under combined_high p1 warm ground increases to 0.01889904200727 while application-optimal mass drops to 0.08397736442977. Noise is not universally monotone in individual metrics, and this is not isolated evidence of a noise benefit.

## Negative and limited evidence

H_workload loses Warm−Std schedule-optimal median mass at both depths (−4.1334 / −12.0117 pp); new_conflict loses −3.3461 / −1.9957 pp; new_capacity loses −2.2892 / −7.9501 pp. All restarts and failures remain included (zero execution failures). These six new synthetic cases and six known diagnostics support descriptive results only. Shots/restarts do not increase the number of independent scheduling problems. No significance, hardware or quantum-advantage inference is warranted.
