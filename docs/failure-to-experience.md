# Failure-to-Experience Pipeline — Phases A–F

Extract failure evidence from existing trajectories and regression results,
group compatible failure events, and review a report with links to the original
evidence. The base package still has no runtime dependencies.

## Quickstart

```sh
pip install -e .
agent-replay experience ingest examples/experience/traces \
  --directory .replay/experience --output .replay/failures.json
agent-replay experience cluster .replay/failures.json \
  --directory .replay/experience --config examples/experience/config.json \
  --labels examples/experience/labels.json --quality-gate
```

The second command prints the paths to `report.json` and `report.html`. Reports
and their referenced objects stay local. Keep the shared `--directory` when
moving from ingestion to clustering; a dataset JSON sidecar alone does not
contain the raw evidence objects.

The shipped sample has ten synthetic trajectories: four repeated signatures
(refund amount, duplicate refund, tool validation, and provider outage) and one
unassigned shipping failure. The refund amount group includes a legacy v1
trace. Sample labels declare `scope: synthetic`; passing that gate is engineering
acceptance, not measured accuracy on a production failure corpus.

## Existing regression evidence

```sh
agent-replay demo --directory .replay/demo
agent-replay experience ingest \
  .replay/demo/traces \
  .replay/demo/before/runs/traces \
  .replay/demo/after/runs/traces \
  .replay/demo/regressed/runs/traces \
  --results .replay/demo/before/results.json \
            .replay/demo/after/results.json \
            .replay/demo/regressed/results.json \
  --directory .replay/demo-experience --output .replay/demo-failures.json
agent-replay experience cluster .replay/demo-failures.json \
  --directory .replay/demo-experience
```

Assertions are attached to `candidate_trace_id`. They are never inferred from
the historical source that seeded a candidate run. Source-only infrastructure
errors may instead match the exact source checksum, including the existing
regression runner's file-byte SHA-256. Missing or ambiguous associations are
retained as rejected evidence.

## Phase A: contracts and resumability

New sidecars live in `sdk/agent_replay/experience/`. Existing trajectory v1/v2, regression, and rollout contracts remain compatible.
Replay gains optional input overrides, state lifecycle adapters and explicit model
fixtures. Existing specs retain their previous behavior.

| Contract | Schema version | Purpose |
| --- | --- | --- |
| FailureRecord | `experience.failure.v1` | Exact source, event location, eligibility, normalized signature |
| FailureDataset | `experience.failures.v1` | Records, input digests, rejected inputs, source counts |
| FailureCluster | `experience.cluster.v1` | Members, representative IDs, similarity, review provenance |
| ClusterSet | `experience.cluster-set.v1` | Complete partition, outliers, configuration and overrides |
| StageManifest | `experience.stage.v1` | Inputs/config/version/seed key, attempt, status, output digest |
| ScenarioSpec | `experience.scenario.v1` | Mutation input, family, parent lineage, environment/verifier refs |
| EnvironmentSpec | `experience.environment.v1` | Registered adapters, state, assets, reset and budgets |
| VerifierSpec | `experience.verifier.v1` | Declarative business assertions and qualification evidence |
| ScenarioResult | `experience.scenario-result.v1` | Candidate outcome, trace, evidence, fidelity and usage |
| DatasetManifest | `experience.dataset.v1` | Export profiles, hashes, disjoint split IDs and exclusions |
| ReviewOverrides | `experience.overrides.v1` | Reviewed membership groups, labels and reasons |
| ReviewedLabels | `experience.labels.v1` | Labeled failure modes and declared synthetic/adjudicated scope |
| ClusterQuality | `experience.cluster-quality.v1` | Pairwise counts, accuracy, support and numeric quality gate |
| EvidenceReport | `experience.report.v1` | Dataset, cluster set, summaries and optional label evaluation |

Definitions in `experience/contracts.py` are the source for both published JSON
schemas and the dependency-free runtime validator. Runtime checks additionally
validate hashes, representative membership, exclusive assignments, stage keys,
review provenance and split overlap. Use `scripts/generate_schemas.py`, then
`scripts/package_release.py`, to synchronize the wheel/plugin copies.

Stages reuse `serialization.digest`, `storage.save`, and `rollout.artifacts.LocalObjects`.
Original and normalized traces are separate content-addressed objects. Rejected
JSON artifacts are retained alongside reasons. This is an additional local
object directory, not a new rollout controller or database.

Stage keys include canonical input digests, implementation version,
configuration, and seed. Input order and exact duplicate inputs do not affect
the key. Successful reruns verify hashes and reuse the completed output;
failed/running manifests retry with an incremented attempt. Corrupted completed
evidence is surfaced rather than silently overwritten. The pipeline is a
single-writer sequential workflow: run separate directories for concurrent
invocations. It does not provide distributed leases or concurrency scheduling.

CLI ingestion reads JSON traces (single files or directories) and accepts
regression result objects, suite reports, or lists. Invalid JSON syntax or
unreadable files returns a command error. Structurally invalid JSON artifacts
are quarantined while valid artifacts continue. Ingestion returns exit `2` if
any artifacts were rejected, and still writes its dataset and manifest.

## Phase B: extraction and clustering

Each observed failure event receives a stable ID based on its immutable source
and evidence location. One trajectory may produce several records. Identical
propagated step/execution errors are counted once; a separately observed
business failure and assertion result retain their separate evidence.

Extraction distinguishes:

- Business assertion violations, including completed runs with failed business rules.
- Tool, model, and agent errors.
- Provider failures, replay mismatches, interrupted runs, incomplete captures,
  and infrastructure-only regression results.
- Manual/custom failures whose domain remains unknown.

Capture eligibility is `eligible`, `blocked`, or `unknown`. It describes the
available recordings, not whether a registered project/environment is runnable.
Legacy coverage gaps remain unknown; redacted inputs, unavailable completed
boundary outputs, and interrupted/incomplete captures are blocked. Later
scenario execution must still perform project and environment preflight.

The signature includes task family (explicit `metadata.task_family_id`, falling
back to the trajectory name), domain, category, exception type, assertion ID,
boundary identity, tool sequence and normalized message. Volatile UUIDs, long
hashes, timestamps and explicitly named request/order/run IDs normalize away.
Amounts, numeric limits, HTTP codes, and operators remain meaningful.

Clustering first groups exact signatures. Within matching anchors, it uses a
deterministic combination of token overlap and character similarity. Different
numeric tokens do not merge. Complete-link merging prevents a chain of partial
matches from joining incompatible endpoints. Default minimum group size is two
and similarity threshold is `0.85`. Outliers remain visible. Unknown-domain
events require review for assignment.

Confidence is the minimum pairwise signature similarity, not a probability of
a shared root cause. Phase B does not generate or claim verified root causes.

## Reviewed merges and splits

An overrides file has `schema_version: experience.overrides.v1` and a `groups`
array. Each group has `member_ids`, `label`, and a `review` object containing
`reviewer` and `reason`. Named members receive their reviewed assignment;
remaining members follow automatic clustering. Multiple smaller groups split
an automatic cluster; one combined group merges clusters.

Overrides cannot reference missing members, assign a member twice, or merge
different domains/task families. Reviewed singletons are allowed. The full
override content is included in configuration/stage identity.

```sh
agent-replay experience cluster .replay/failures.json \
  --directory .replay/experience --overrides reviewed-groups.json
```

## Label evaluation and quality gates

A labels file declares `schema_version: experience.labels.v1`, `scope`
(`synthetic` or `adjudicated`), `review` (`reviewer`, `reason`), and a `labels`
array of `{failure_id, mode}` entries. Mode labels should distinguish the task
families used by the clustering definition. Labels are saved as evidence
objects and their digest is included in the report. Reports with different
labels get separate default report directories.

Precision is correctly co-clustered labeled pairs / all co-clustered labeled
pairs. Recall is correctly co-clustered labeled pairs / all same-mode labeled
pairs. Unassigned records form individual predicted groups. The report includes
true/false positive pairs, false negatives, labeled and unlabeled counts, and
the declared label scope. Zero denominators remain `null`/`not_evaluated`.

With `--quality-gate`, numeric targets are precision ≥0.90 and recall ≥0.80.
Exit codes are `0` for passing targets, `1` for failed targets, `3` for unmeasured
targets, and `2` for invalid inputs or infrastructure errors. Without that flag,
cluster reporting succeeds regardless of optional accuracy results. No replay
or regression verdict/exit code is changed.

Pair counts share records and are statistically dependent. This release does
not attach binomial confidence intervals to them. Accuracy on an independently
adjudicated corpus and uncertainty estimated at a suitable task-family sampling
level remain an external validation requirement. The shipped ten-trace sample
and the 1,000-event scale test validate deterministic engineering behavior.

## Public interfaces and remaining phases

```python
from agent_replay.experience import extract_failures, cluster_failures, validate
from agent_replay.experience.pipeline import ingest, cluster
from agent_replay.experience.clustering import evaluate_labels

records = extract_failures(trace, regression_results=None)
cluster_set = cluster_failures(records, config=None, overrides=None)
# ingest/cluster additionally persist content-addressed objects and stage manifests.
```

Refund mutation, compilation and verifier qualification are implemented below.
Refund evaluation and portable RL-ready scenario/experience export are implemented
below. A small live cross-model evaluation is recorded in the published aggregate report
(see "Recorded live evaluation" below). Production generalization and training-benefit
evaluation remain unmeasured. Existing replay/rollout/training entrypoints remain the execution
infrastructure; no trainer or new controller is introduced.

## Verification

```sh
python -m unittest discover -s tests -v
node --test tests/*.test.mjs
python scripts/package_release.py --check
```

The existing workflows retain their replay, regression and rollout checks.
`experience.yml` adds an offline sample extraction/clustering/quality-gate run
and publishes its evidence. Unit tests cover normalization, source immutability,
candidate binding, corrupted/missing objects, recovery, order invariance,
complete-link grouping, outliers, review controls, labels, safe HTML, schema
publication, and a 1,000-event known-label corpus.


## Phase C: refund mutation and executable cases

```sh
agent-replay experience refund-demo --directory .replay/refund --seed 0
```

This key-free synthetic acceptance story runs the original regression demo,
ingests and clusters its failed source, creates eleven refund scenarios,
qualifies their verifiers, and executes each through the existing regression
suite. The aggregate exit codes must be `[1, 0, 1]`. The command returns `0`
only after the whole story succeeds; invalid or unavailable evidence returns `2`.
The generated cases can also be run directly by the existing `agent-replay test`
command using their compiled project registry and case paths.

For explicit reviewed policy data and an existing eligible refund cluster:

```sh
agent-replay experience mutate .replay/failures.json .replay/clusters.json \
  --cluster-id <cluster-id> --requirements examples/experience/refund-policy.json \
  --directory .replay/experience --seed 0 --budget 11 --output .replay/scenarios.json
agent-replay experience refund-loop .replay/scenarios.json \
  --source <parent-source-trace.json> --directory .replay/experience
```

Use the same object directory as ingestion. Mutation requires application-domain,
eligible failure evidence with observed `payments.refund` boundaries. It checks
cluster membership, and compilation requires an immutable source from that
cluster. Explicit requirements identify their policy authority; they are supplied
by the user, never inferred from a failed agent's response. The shipped policy
is synthetic and uses integer minor currency units.

The operator registry lives in `experience/mutation.py`. Supported operators are
request below/at/above the approval or remaining-payment boundary, identifier
substitution, wording, duplicate opportunity, transient lookup timeout, partial
lookup response, transient not-found lookup, missing optional wording, and
conflicting instructions. Required order and amount fields stay present;
persistent not-found, contradictory structured policy values and arbitrary
new tool shapes are outside this initial refund adapter. Preconditions require
at least two minor units of refundable value so every partial-amount negative
is distinct. Approval/payment balances, currency and one-refund semantics remain
invariants. The below-boundary operator additionally needs a cap of at least three
minor units; otherwise it is skipped. An empty applicable proposal set is rejected. A local seeded RNG determines proposal order; logical input/state
hashes remove duplicates; the per-parent budget is bounded to 1–100.

`mutate` uses the existing content-addressed stage store. The stage key includes
policy, parent cluster, failure dataset, operator selection, budget, seed and
implementation digest. Scenario/environment/verifier IDs and cross-references
are validated before compilation. Compiled project, source, case and sidecars
are immutable; mismatched existing contents raise an error.

The compiler emits standard `refund.case.json` and `agent-replay.json` files.
The replay spec includes an explicit `input_override`, seed, local lookup/payment
rules and registered reset/snapshot/cleanup adapters. The original source is
copied without changing its content, and both source and resolved replay spec
remain part of existing experiment identity. Each execution creates fresh state,
collects an independent snapshot, then cleans it up even after an agent error.
No real payment service is called. Changed inputs with no matching fixture or
registered local tool still fail closed through the existing matcher.

Model fixtures now explicitly work at `resolve_llm` as full response objects,
with the existing consumption/expected-call checks. They do not use live model
calls. Refund examples do not need a model; cross-model tests require a separate
opt-in live experiment.

## Phase D: verifier generation and qualification

`experience/verifiers.py` compiles trusted policy into the existing declarative
assertion operators: completed execution, no unhandled error, exactly one refund,
correct order, approval/remaining/request bounds, exact permitted amount, the
actual ledger record, and the final state transition with other orders unchanged.
Snapshot identity pins the environment and simulator version. Agent output such
as `{ok: true}` cannot substitute for a ledger change. Missing/incompatible state
or incomplete capture returns `inconclusive`; evaluator failures return `error`.

`agent-replay experience qualify <bundle> --source <trace> --directory <objects>`
qualifies verifiers without requiring the regression story. It exits `1` if any
qualification is rejected, `2` on invalid inputs, and `0` when all qualify.

Every scenario needs an independent constructive positive witness plus mandatory
negatives for wrong entity, excess amount, skipped action, duplicate action,
partial amount, misleading success and failure after payment. Witnesses execute
through the same registered local environment and existing `run_case`; no trace
is edited to manufacture a label. Positive and business-negative witnesses must finish execution; worker/tool
errors cannot count as successful counterexample rejection. The partial-execution
witness must reach payment before the expected error. Each witness's identical evidence is evaluated
100 times for stability. This is evaluator determinism, not 100 independent
model/agent executions.

A qualification sidecar binds the scenario, policy requirement, environment,
verifier, source and installed execution/evaluator implementation digests. It
also retains candidate traces, regression results and resolved spec hashes in
the existing object store. Promotion rechecks every mandatory witness and its
bindings. Changing requirements, environment, assertions, source or installed
implementation invalidates prior qualification. Completed qualification can be
reused only after those checks. Rejected/incomplete verifiers cannot be promoted.
The immutable verifier remains a draft definition; the separate qualified
sidecar supplies execution eligibility, avoiding circular content hashes.

`refund-loop` promotes only qualified cases, then uses `run_suite` for broken,
fixed and reintroduced-broken candidates. Each phase retains the existing JSON,
HTML and JUnit reports, plus `experience.scenario-result.v1` evidence. Its report
shows execution and qualification rates, mandatory negative rejection and fixed
success rates. These are synthetic engineering measurements; new production
failure discovery, adjudicated mode coverage, cross-model replication and
held-out training improvement remain unmeasured. These qualified scenario/result
sidecars feed the Phase F exports below.

```python
from agent_replay.experience.pipeline import mutate
from agent_replay.experience.compiler import compile_bundle, qualify, refund_loop
from agent_replay.experience.verifiers import evaluate_verifier

bundle = mutate(dataset, parent_cluster, requirements, directory=directory)["value"]
qualification = qualify(bundle, parent_source, directory)
cases = compile_bundle(bundle, parent_source, directory, qualifications=qualification)
report = refund_loop(bundle, parent_source, directory)
```

The existing CI workflows stay intact. `experience.yml` adds the offline refund
acceptance story and publishes its artifacts alongside A/B evidence. Unit tests
cover source preservation, mutation identity and budgets, altered artifacts,
positive/negative witnesses, qualification invalidation, missing state, lifecycle
cleanup, model fixture exhaustion and unchanged regression reports.


## Phase E: evidence-based refund evaluation

```sh
agent-replay experience evaluate .replay/refund/scenarios.json \
  --report .replay/refund/report.json --directory .replay/refund \
  --output .replay/refund/evaluation.json
```

`experience.evaluation.v1` includes numerator, denominator, scope and measurement
state for execution, decisive verdicts, success, qualification, mandatory-negative
rejection, identical-evidence stability, repeated-candidate verdict consistency,
and failure reproduction across distinct candidate identities. Candidate tables
retain versions, verdict counts, recorded durations and actual model-call usage.
Overall success pools candidates; it is not a training improvement estimate.
Operator coverage and observed failure signatures are reported separately.

Reports are recomputed from content-addressed ScenarioResult and trace evidence,
not the C/D report's summary percentages. The evaluator verifies artifact IDs,
scenario/environment/verifier bindings, source and replay-spec provenance, result
associations, verdicts, usage, and current qualification. Exact duplicate result
artifacts count once. Errors/inconclusive runs are retained as diagnostics rather
than converted into successful results. Missing or stale qualifications are
explicit exclusions; corrupt/missing artifacts and forged verdicts are errors.

Without a measured baseline, new-failure-mode rate is `null/unmeasured`. Supply
`--baseline <stored-evaluation.json>` to compare failed-assertion signatures. The
baseline must exist in the same object store and its original evidence is
re-evaluated before use. This measures assertion-signature novelty, not reviewed
root-cause novelty. Cross-model reproduction and held-out training improvement
remain `null` for the key-free refund story: it has no independent live model
runs or training intervention. Repeated broken runs are not different models.
Durations are observations, not a controlled performance benchmark. Correlated
synthetic cases do not support independent-sample accuracy confidence claims.

## Phase F: portable qualified datasets

```sh
agent-replay experience export .replay/refund/scenarios.json \
  --report .replay/refund/report.json --directory .replay/refund \
  --profile evaluated-experience --destination .replay/refund-export --seed 0
agent-replay experience verify-export .replay/refund-export
```

Two profiles are implemented for this refund adapter:

- `scenario`: qualified executable environment curriculum, with no invented
  candidate trace, verdict, reward, tokens or log probabilities.
- `evaluated-experience`: completed, captured, qualified agent experience with
  binary reward `pass=1`, `fail=0`. Errors, inconclusive, unavailable/redacted
  inputs or completed outputs, unsupported fidelity, and stale evidence are
  excluded with reasons. Handled lookup failures with complete final execution
  remain valid experiences. This is an algorithm-neutral evaluated dataset.

`policy-rollout` is rejected for refund data. The existing rollout/training
contracts need actual policy actions/tokens, behavior log probabilities and
policy versions; tool-only refund traces do not provide them. No synthetic
policy metadata is fabricated, and no existing trainer is changed.

Each export contains `manifest.json` (`experience.dataset.v1`), `records.jsonl`
(`experience.export-record.v1`), `evaluation.json`, and a self-contained `objects/`
store. Records bind their scenario, source trace, parent failures/cluster,
requirement, environment, verifier, qualification, candidate/trace/result and
reward to immutable digests. The object closure retains raw and normalized
source evidence, failure records/dataset, all scenario definitions, qualification
witness traces plus regression evidence, and candidate results/traces. Manifest
file checksums cover every listed artifact. Moving the export needs no access
to the original run directory; the installed trusted refund runtime is still
required to interpret executable adapters and verify qualification versions.

Split assignment uses deterministic connected groups across task family, shared
parent failures/cluster, and equivalent policy/task shape. Wording and entity-ID
substitution cannot create independent splits. Every sibling and all candidate
runs of one scenario stay together. `--split-ratios TRAIN VALIDATION TEST` defaults
to `0.8 0.1 0.1`, but whole groups take precedence over quotas. The eleven-scenario
refund demo has one connected family, so its 33 experiences all go to train;
validation/test are explicitly empty. It cannot supply a held-out improvement
claim. Independent families are needed for meaningful held-out evaluation.

Export creation is atomic. The same destination is reusable only for identical,
fully verified contents. Different contents, checksum drift, path traversal,
symlinks, unlisted files, omitted eligible records, modified rewards or cross-split
lineage are rejected. Empty eligible sets yield a blocked manifest with exclusions.
`verify-export` rechecks eligibility, qualification witnesses and verdict/reward
bindings from the export's own store without executing agents. `--output` sidecars
must be outside the immutable export directory. Command errors retain exit `2`;
existing replay/regression/CI verdicts and exit codes are unchanged.

```python
from agent_replay.experience.evaluation import evaluate, write_evaluation
from agent_replay.experience.export import export_dataset, validate_export, load_dataset

metrics = evaluate(bundle, refund_report, directory=run_directory)
exported = export_dataset(bundle, refund_report, destination,
                          directory=run_directory, profile="evaluated-experience")
dataset = load_dataset(destination)
# dataset["records"] and dataset["objects"] expose verified data to a consumer.
```

The offline experience CI job now evaluates, exports and independently verifies
its dataset in addition to A–D checks. Other CI workflows remain intact. E/F tests
cover explicit denominators/unmeasured states, baseline reproducibility, duplicate
results, preserved lineage/witnesses, relocated stores, split leakage, stale or
missing qualification, profile eligibility, deterministic reuse, forged rewards,
corruption, omitted records, path safety and published schema compatibility.

### Multiple refund families and held-out evaluation

`experience refund-families --directory .replay/refund-families --destination .replay/refund-curriculum`
executes 12 separately captured synthetic policy families (approval, request, remaining balance,
prior refunds, small/large amounts and multiple currencies), with two mutations each by default.
These are independent parameterized simulator tasks, not evidence of production diversity.
Every source capture uses that family's actual policy and payment state; each scenario goes
through the existing eight-witness qualification and existing broken/fixed/regressed suites.
The policy-preserving mutations retain request-cap tasks instead of replacing every request
with approval + 1. No runtime, regression exit codes, or default live-call policy changes.

A `refund-collection.v1` manifest composes portable, independently verified v1 exports in
`children/<digest>/`. `experience.collection-record.v1` records keep the original child record
ID and evidence digests while assigning global splits. `validate_collection(path)` reproduces
all child evidence, the exact record inventory and global assignments from the export alone.
The union-find groups family, parent cluster, parent failures, raw/normalized source digests,
and equivalent policy/lookup tasks. Names and substituted order IDs do not create independence.
Assignment reserves the last groups for still-empty requested splits. Export fails rather than
splitting connected siblings when fewer independent eligible groups than active splits exist.
Destination is immutable; use a new directory for a new generation.

```
agent-replay experience verify-collection .replay/refund-curriculum
agent-replay experience held-out .replay/refund-curriculum --output .replay/held-out.json
```

Held-out comparison uses only validation/test and pairs the existing broken/fixed executions
on exactly the same qualified scenarios. Each scenario is successful only when all its
candidate repetitions pass. Reports retain success numerators, scenario denominators, result
digests and the dataset manifest digest. This is an implementation comparison, not an RL
training improvement; the latter remains explicitly unmeasured.

### Opt-in cross-model reproduction

Copy `examples/experience/refund-models.json`, supply actual model IDs, pinned version labels,
provider endpoints and credential **environment variable names**. Do not put secrets in JSON.

```
agent-replay experience cross-model .replay/refund-curriculum --config models.json --destination .replay/model-runs --live
agent-replay experience verify-models .replay/refund-curriculum --experiments .replay/model-runs
```

`run_models(dataset, config, output)` calls the existing registered provider through the existing
bounded worker, with 1 model call and 256 output tokens per run, a 60-second timeout, 2–5 distinct
provider/model pairs and 1–10 repetitions. It never runs train scenarios. The model receives
public task input and actual lookup results only; witness amounts, reference answers and
qualification artifacts never enter the prompt. The model's JSON action determines the actual
payment; the trusted verifier evaluates the resulting ledger and state transition.

`evaluate_models(dataset, experiments)` checks the exact planned execution matrix, source,
resolved replay spec, input, live model boundary, provider response, implementation digest,
qualified scenario and dataset binding, then recomputes verdicts. Missing/duplicate executions,
train injection and fixtures masquerading as live boundaries are rejected or nondecisive.
Infrastructure errors are excluded from business failure reproduction. Success rates use
pass/(pass+fail), alongside planned/decisive coverage and error/inconclusive counts. Cross-model
reproduction counts common decisive scenarios failing on at least two distinct model identities
in **every** configured repetition; the denominator is all common decisive held-out scenarios.
`offline-wire-test` scope exercises HTTP and workers but never produces a measured cross-model
metric. Model version labels are caller supplied and actual responses remain in the evidence;
no training, behavior log probabilities or policy action tokens are fabricated.

Live experiments are separate evidence sidecars, not silently inserted into the qualified v1
curriculum. The collection remains an evaluated-agent-experience/environment curriculum for
training adapters to consume, not a claim of policy-rollout readiness.

### Public refund summary

Generate the main-site summary only from a verified portable collection:

```
python scripts/publish_refund_summary.py .replay/refund-curriculum dist/refund/evaluation.html
python scripts/package_release.py
```

The page lists task families and their actual split membership, scenario/record counts,
qualified verifiers, and paired held-out rule-candidate success denominators. Reports generated
without `--model-experiments` mark cross-model measurements as pending. To include live
results, provide the original local execution evidence for independent verification:

```sh
python scripts/publish_refund_summary.py .replay/refund-curriculum dist/refund/evaluation.html --model-experiments .replay/model-runs
python scripts/publish_refund_summary.py .replay/refund-curriculum dist/refund/evaluation-en.html --language en --model-experiments .replay/model-runs
```

Rule-candidate improvement is not an RL training claim.
Main, replay and benchmark navigation link to `/refund/evaluation.html`. Nested report
HTML/CSS are included in the SDK and plugin web assets. Production publication includes
only static site assets and this aggregate page; source/data ZIPs and raw evidence remain
local and are excluded from the deployment file manifest.


### Recorded live evaluation

The checked-in bilingual aggregate reports and README record 12 synthetic task
families, 24 executable scenarios and 72 evaluated records. Validation and test
each contain two scenarios. Two pinned models ran three repetitions per held-out
scenario, yielding 24 responses and no provider errors:

| Model | Validation successes / runs | Test successes / runs |
| --- | --- | --- |
| `gpt-4.1-mini-2025-04-14` | 6/6 | 0/6 |
| `gpt-4o-mini-2024-07-18` | 0/6 | 0/6 |

Stable cross-model failure reproduction was 2/4 shared scenarios (50%). Eleven of
the eighteen failed executions violated the plain-JSON protocol with Markdown
code fences; JSON response mode and automatic fence stripping were not enabled.
These are end-to-end results on four synthetic held-out scenarios, not a model
ranking, evidence of production generalization, or measured RL training gains.

The repository contains aggregate reports, not the original live execution store.
Reverification or regeneration of these live metrics requires that local evidence
and the matching portable collection; offline CI does not reproduce live results.
