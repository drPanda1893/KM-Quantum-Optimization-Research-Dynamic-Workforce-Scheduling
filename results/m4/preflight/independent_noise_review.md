# Independent M4 noise review

1200 saved observations / 240 fixed training circuits, source `27f2b9e82fd76394f9860f455953eacc66b3cc42`. No simulation or optimization performed. Frozen circuit, angles, readout and TV checks PASS.

Each value below is the median over six equally weighted instance medians of five paired restart changes. Units: percentage points; after readout. This is distinct from the difference of restart medians.

| Group | Algorithm | p | Gate only schedule / ground | Readout only schedule / ground | Combined low schedule / ground | Combined high schedule / ground |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| known_diagnostic | standard | 1 | -0.3040 / -0.2476 | -0.4495 / -0.3828 | -0.7429 / -0.6217 | -2.3804 / -1.9896 |
| known_diagnostic | standard | 2 | -1.0795 / -0.7612 | -0.6055 / -0.4765 | -1.6091 / -1.2147 | -5.1380 / -4.0862 |
| known_diagnostic | warm_start | 1 | -0.3769 / -0.2937 | -0.4407 / -0.4568 | -0.8645 / -0.7372 | -2.5956 / -2.3069 |
| known_diagnostic | warm_start | 2 | -1.6112 / -0.9978 | -1.0634 / -0.7777 | -2.9377 / -1.7236 | -9.1274 / -5.4182 |
| new_evaluation | standard | 1 | -0.2151 / -0.2151 | -0.6301 / -0.6315 | -0.9502 / -0.9204 | -2.9231 / -2.7903 |
| new_evaluation | standard | 2 | -0.4999 / -0.2637 | -0.9389 / -0.8403 | -1.6659 / -1.3281 | -5.1229 / -4.0482 |
| new_evaluation | warm_start | 1 | -0.3241 / -0.3180 | -0.7582 / -0.7952 | -1.1794 / -1.2156 | -3.6352 / -3.7158 |
| new_evaluation | warm_start | 2 | -0.3504 / -0.4396 | -0.6834 / -0.7046 | -1.2907 / -1.4330 | -3.9246 / -4.3951 |

Warm circuits have exactly twice the compiled sx/x count of standard circuits in the matched structural pairs. CX counts are unchanged; counts include state preparation and matched warm mixers. This is additional synthetic-channel exposure, not an isolated causal explanation of performance.

For K, standard p2 ground median rises from 0.007346833 to 0.011472685 under combined_high while scheduling-optimal median falls from 0.081070351 to 0.069482424. Warm p1 ground median rises from 0.014308434 to 0.018899042, but its median paired ground change is -0.065443 pp: three seeds decline and two rise, with rank changes. Do not conflate these statistics.

Explanations remain descriptive. The profiles are synthetic, training was ideal, and fixed-parameter noise transfer does not establish performance under noisy training or hardware.
