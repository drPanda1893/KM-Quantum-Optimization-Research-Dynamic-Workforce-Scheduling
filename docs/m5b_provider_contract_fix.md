# M5B provider contract correction

Review baseline: `f31d4ee38372338d43f17cc2e301a2051b0f2a55`, fix branch
`fix/m5b-preflight-contracts`. Documentary limits were reviewed on 2026-10-12;
the policy records the retrieval timestamp and source URLs. The installed pins
remain Qiskit 2.5.2, Aer 0.17.2 and IBM Runtime 0.47.0.

The old gate interpreted nullable BackendConfiguration fields as missing owner
credentials. The replacement distinguishes live account facts, documented
service limits, concrete local payload assessment and local usage estimates.
It never treats an unknown value as unlimited capacity.

## Documentary service policy

[IBM job limits](https://quantum.cloud.ibm.com/docs/en/guides/job-limits) explicitly
retires `max_shots` and `max_experiments`. Sampler permits 10 million executions
per job after PUB broadcasting. The documented instruction-memory heuristic is
26.8 million control instructions per qubit. Native per-circuit ceilings are
30 million RZ, 20 million SX and five million two-qubit gates. The published
weights are RZ/delay 1, SX/X 2, CX/CZ/ECR 5, measure 10, reset 17 and init 50.

The [jobs REST contract](https://quantum.cloud.ibm.com/docs/en/api/qiskit-runtime-rest/tags/jobs)
documents five job submissions per minute per user. This is admission rate,
not the number of jobs allowed in a campaign. A concurrent-job count and a
cumulative provider campaign-job allowance are not exposed by these reviewed
sources. Accordingly, they remain `None` with `status="not_exposed"`.
BackendConfiguration `max_jobs` is retained as a nullable observation and no
longer gates the experiment. The experiment's independent total remains three
serial provider jobs, 49,664 shots, 40 QPU seconds per job, 120 seconds reserved
in total, and zero euros. None of these budgets increased.

`service_limit_policy()` seals this documentary policy. Altered numbers,
missing fields or an invented service limit produce
`PROVIDER_CONTRACT_ERROR`, not a credential-reset request.

## Concrete native payload and circuit assessment

`assess_plan_limits(plan, circuits, qpy_hashes=...)` is entirely offline. The
caller loads and verifies the actual QPY files. The assessment binds their raw
hashes, persistent PUB order, exact shots, named `m2` width, provider circuit
width, Runtime options, layout, backend and instance pseudonym. It copies the
circuits before inserting the same per-PUB metadata used by the ledger.

Actual Qiskit `SamplerPub.coerce` broadcasting determines expanded circuits and
executions. The generic counting helper demonstrates broadcasting explicitly;
the fixed M5 inventory permits only already-bound circuits with scalar PUB
shape. Unknown native operation weights, changed QPY hashes, unexpected
parameter expansion and failed gate/control/execution limits block.

For instruction memory this client uses a deliberately conservative local
bound: native weights are multiplied by each PUB's shots and initialization is
included on every provider wire. This is a disclosed upper-count scenario,
not an assertion about IBM's actual loop-memory allocation. Idle full-device
wires are counted without allocating a full-device statevector.

The SDK 0.47 `SamplerOptions` and `RuntimeEncoder` serialize the real circuit
objects, metadata, options, backend, cost cap and the ledger's fixed-length
tags. The report records the resulting body size and a separate 1 KiB local
allowance for optional SDK envelope fields, including calibration context.
The combined value must fit the independent 1 MiB client guard. A provider
input-byte maximum was not established by the reviewed source: it remains
unknown. This local guard does not promise server acceptance. Neither a guessed
50 MB ceiling nor QPY file size is used as a verified server input limit.

The sealed `provider_limit_assessment` and its content hash become part of the
plan. `validate_limit_assessment` checks the seal, submission-input binding,
native per-PUB inventory, shots, ordering, gate counts, control metrics and
payload guard. It cannot replace the caller's raw QPY-file verification.

## Live account and reservation gates

Only an explicitly requested preflight constructs the service. The constructor
uses the exact named account or explicit token, exact instance, and exact
backend. It neither selects a default account nor calls `least_busy`. Import,
option validation, payload assessment and model validation create no service.

Fresh evidence still requires free Open Plan, zero cost, real non-simulator
backend, operational status, accepting status message `active`, explicit
provider-returned instance region, exact pseudonymous binding and the reviewed
single-job SDK mode. Usage numbers must be finite, nonnegative, consistent and
at most five minutes old. `usage_limit_reached` must be explicitly false even
when the SDK's instance-allocation subtraction produces positive remaining
seconds. Full future reservations must fit the observed remaining quota.

In installed SDK 0.47, `jobs(pending=True)` documents only QUEUED and RUNNING.
The adapter instead reads `jobs(limit=101,pending=None,instance=explicit)` and
reads each returned status once, including INITIALIZING. An unknown status or
101 returned jobs makes this bounded inventory incomplete. A complete empty
non-final inventory establishes zero currently observed reservations for the
selected instance. Any non-final job, failed inventory read or incomplete
inventory leaves reservations unknown and blocks. No unbounded scan or polling
loop is added; this observation is a snapshot, not a provider transaction or a
cross-account concurrency guarantee. Fresh evidence is required again before
any separately authorized submission.

The complete sealed `local_usage_model` and its execution-estimate map are
retained in the fresh evidence. Validation binds them to the exact plan, QPY
inventory, Target and repetition delay. A lone externally supplied seconds
value cannot make a plan approvable. A valid local scenario exceeding the
unchanged caps reports `USAGE_LIMIT_EXCEEDED`; missing or changed model facts
report `INSUFFICIENT_USAGE_MODEL`. Neither is described as an IBM quotation.

## Diagnostics, secrets and unchanged execution state

`SafetyError.diagnostic_kind` distinguishes owner configuration/admission
problems from provider contracts and estimation failures. Preflight preserves
successfully read facts when a later local gate fails, with `status="BLOCKED"`
and typed diagnostics. An actual owner configuration/admission failure retains
`OWNER_CONFIGURATION_REQUIRED`. Private credentials and CRNs are redacted from
both paths.

Named-account redaction reads only an already-created SDK account object's
private token into an in-memory redaction set. It reads no account file or
credential dump. If a named-account constructor fails before that token can be
identified safely, its opaque SDK detail is suppressed. Recursive key/text
redaction and the instance-binding hash remain in place.

Git's local `info/exclude` alone is insufficient for the source-distribution
builder. An actual package inspection found the private binding directory in
the first local sdist. The archive was never uploaded. The sdist configuration
now explicitly excludes `.m5-local` and run-result directories; the regenerated
wheel and sdist are inspected for private binding/account members. Dependency
pins remain unchanged. The failed inspection is preserved separately in
`build_privacy_before.json`; the corrected evidence is `build_inspection.json`.

No approval, submit, collect or ledger semantics are weakened by this change.
Historical artifacts remain unchanged. New plans still have
`hardware_enabled=false` and `submission_approved=false`. Tests use synthetic
account/backend objects and explicitly deny provider HTTP requests.

Separate known SDK aspect, outside this three-blocker fix: Runtime 0.47's REST
`program_run` supplies an explicit 900-second POST timeout, overriding the
session's default timeout. This task does not alter that transport behavior.
The existing no-retry transport and persistent ambiguous-submission ledger
continue to prevent automatic resubmission. No submission occurs in this task.

## Regression evidence

`results/m5b-fix-20261012-v1/provider_red.txt` captures the initial three failing
regressions for obsolete fields, the named-account opaque secret and the
account exhaustion flag. `provider_green.txt` captures the corrected targeted
provider and contract suite. Additional regressions cover actual PUB
broadcasting, native payload size, instruction/execution ceilings, immutable
plan bindings, pending/unknown/truncated job inventory, typed diagnostics and
the complete Target-model boundary. All are offline.

## Acceptance diagnostic follow-up

The v1 read-only run produced a structurally bound three-job, 52-PUB draft.
Its conservative local usage scenario exceeded the unchanged caps. Acceptance
correctly blocked execution, but incorrectly discarded the draft's planned
quantities and reported missing owner configuration despite the separately
authenticated account observation. This was a reporting defect.

The acceptance builder now retains only diagnostic planned quantities from a
structurally validated, sealed draft bound to the verified circuit inventory.
The original executable-hardware-plan, ledger, approval and result gates are
unchanged. A draft never enters those validated execution paths. Failed
structure or inventory binding still supplies zero trusted planned counts.

Cached preflight facts are allowlisted and reported as historical observations,
with their original timestamp. `VERIFIED_HISTORICAL` requires matching backend,
instance pseudonym and exact plan hash. The report explicitly performs no live
read and claims no fresh submission eligibility. Typed usage, provider-contract,
local-channel and calibration failures remain visible. Credential-bearing SDK
free text, headers and service objects are excluded; final output is redacted.
Neither these facts nor the displayed draft quantities remove `BLOCKED` or
grant human approval.

The new regression first failed in three typed-diagnostic cases and passed two
existing safety assertions. The red evidence is retained under
`results/m5b-fix-20261012-v2/readiness_diagnostics_red.txt`; targeted green and
tooling evidence use that v2 directory. The follow-up changes source-context
hashes. Full QA and the authorized read-only CLI are rerun into a fresh v2
output. The source-bound v1 preparation and provider artifacts remain archived,
rather than relabeled or refreshed.

The independent v1 artifact review also preserves its initial strict-byte
comparison false alarm. `independent_review_v2.json` corrects that audit
interpretation transparently: all 84 stored raw-QPY hashes and the archived
assessment's own seal match exactly; every regenerated semantic limit field
matches. Fresh RuntimeEncoder/QPY serialization varies by a few bytes while
remaining far below the independent client guard. This observation does not
establish a canonical SDK serialization identity, a universal variability
bound or an unpublished provider-byte limit. The reviewed v1 hardware draft
remains blocked and unapproved.
