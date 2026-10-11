# Verified regression test vertical slice

This is an offline/local prototype for design-partner validation, not the complete
self-service/billing SaaS. It is stacked on PR #3. Native benchmark/replay/regression
commands remain available; host executable replay remains disabled.

## Reproducible demo

```sh
python -m pip install -e '.[dev]'
agent-replay guard demo --directory .replay/guard-demo
```

The bundled deterministic coding toy agent uses data-only recorded read/patch tools.
Version A selects addition; version B selects multiplication and fails the task
oracle. Capture sanitizes the synthetic Authorization token, import stores only v2
sanitized evidence, generation extracts three complete recorded fixtures, explicit
expected output defines the verifier, A passes and B fails the verifier, and the
synthetic reviewer approves the exact case version. Candidate evaluation returns
PASS, FAIL and blocking INCONCLUSIVE for absent candidate evidence. No model/provider
or production tool is called. `ci-input.json` identifies the resulting case/traces;
`demo-result.json` points to the existing JSON/HTML/JUnit reports. Canary scanning
covers captures, journals, fixtures, cases, approval and all reports.

The GitHub `Agent Regression Guard / failure-to-test` job verifies that the synthetic
FAIL exits 1, publishes the decision in the job summary, and retains sanitized
synthetic artifacts seven days. That validation job is green when blocking works.
For a customer release job invoke `guard run` directly (without swallowing its exit
code) and configure it as required. No branch protection or PR comment bot is installed
automatically. The example is [examples/guard/github-actions.yml](../../examples/guard/github-actions.yml).

## Explicit workflow

```sh
agent-replay guard import failure.json --format native --directory .replay/project
agent-replay guard import known-good.json --format native --directory .replay/project
# Use trace IDs returned by import; expected.json must be reviewed by the operator.
agent-replay guard generate --source FAILED_TRACE_ID --positive GOOD_TRACE_ID \
  --expected examples/guard/expected.json --directory .replay/project
agent-replay guard show CASE_UUID --directory .replay/project
agent-replay guard approve CASE_UUID --reviewer joe --directory .replay/project
agent-replay guard run CASE_UUID --baseline GOOD_TRACE_ID --candidate NEXT_TRACE_ID \
  --directory .replay/project --output .replay/gate.json
agent-replay guard usage --directory .replay/project
```

Generation without a clear oracle, discriminating positive/negative pair, complete
task input or complete tool responses returns an incomplete draft (exit 3). Approval
requires explicit reviewer identity; HTTP uses the authenticated ADMIN subject.
Versions are immutable `guard/cases/<case>/versions/<n>.case.json`, with separate
`approval-<n>.json`. Regenerate via `generate --case-id CASE_UUID`; a new version is
a draft until reviewed. Changed sources/rules/fixtures/validation invalidate approval.
Old generated versions cannot bypass approval through `agent-replay test`.

Runs are persisted under `guard/runs/<run>/`, including comparison evidence,
recorded `playback.json` for inspection, gate.json and existing regression reports.
Gate stdout prints decisions/references, not full trace payloads. Exit codes: PASS 0,
FAIL 1, invalid/config/quota error 2, blocking INCONCLUSIVE 3.

## Supported inputs

Native: existing v1/v2 trajectories. IDs must be portable strings (80 characters
maximum). All formats are limited to 1 MiB and 200 steps in this guard adapter.
Canonical v2 is reused, not replaced. Model/prompt/tool/environment versions may be
stored in metadata; `agent_id` and `agent_version` are required for generation and
candidate evaluation. Organization/project metadata is server-derived.

Generic JSON: id, name, started_at, status (completed/error/interrupted/running),
duration_ms, input/output and steps. Steps have id, parent_id, kind (agent/llm/tool),
name, status, start_ms, duration_ms, input/output. Optional failures classify explicit
business failure. Missing input/output stays unsupported. Relationships are validated.

OTLP JSON: one complete trace in resourceSpans → scopeSpans → spans. Supports hex
trace/span/parent IDs, nanosecond timestamps, status, scalar/array/kvlist attributes.
Missing parents, duplicate spans, mixed trace IDs and unsupported values reject.
Payload attributes are explicit `agent_replay.input` / `agent_replay.output` (JSON
strings or JSON AnyValue structures), not automatically inferred from arbitrary
vendor exports. `gen_ai.tool.name` identifies tool spans; `gen_ai.request.model`
identifies model spans. `agent_replay.kind` overrides the mapping. Root/service
attributes map `agent_replay.agent_id` / service.name and
`agent_replay.agent_version` / service.version. This is a tested OTLP file subset,
not a live collector or a complete GenAI semantic-convention integration.

## Verifier vocabulary

Reuse existing equals/not_equals/exists/numeric/contains/count/forbidden/execution
assertions. Add `json_schema`, `ordered` and `predicate` to that same engine. JSON
schema supports type/properties/required/additionalProperties/items/enum/const and
numeric/length/item bounds; no references, regex, external schema fetch or generated
code. Predicates support bounded all/any groups and JSON-pointer equals/exists/
numeric/contains checks. Ordered rules require unique sequential selections and
reject ambiguous lanes or overlapping timing. Test on supplied positive and negative
examples; confidence is `example_pair_only`, not a calibrated probability.

## Existing HTTP API extension

Run the existing loopback server with `--access-config` to exercise tenant isolation.
No new web framework or remote public binding is introduced.

| Method / path under /api/v1 | Meaning |
| --- | --- |
| POST guard/import | trace + format; sanitized, principal-scoped import |
| GET guard/cases | latest generated versions and review status |
| POST guard/cases | source_id, positive_id, expected; optional case_id for a new version |
| GET guard/cases/:id | inspect verifier/fixtures/validation/review |
| POST guard/cases/:id/approve | ADMIN approves exact version; optional version |
| POST guard/runs | case_id, baseline_id, candidate_id, optional version; evidence-only evaluation |
| GET guard/runs/:id | persisted gate and comparisons |
| GET guard/usage | server-enforced project usage and limits |

VIEWER reads; DEVELOPER imports/generates/evaluates; ADMIN approves. Body org_id,
reviewer and billing/limit claims never change principal or entitlement. Operator
access config can supply guard_limits and sensitive_fields. Default lifetime caps:
100 stored imports, 50 generated versions, 500 runs, 64 MiB artifacts. Duplicate
identical imports/approvals are idempotent. No model use, automatic retries or
multiple unbounded background jobs are introduced. New guard mutations use an OS
file lock; unsupported locking platforms reject mutations. Existing organization
imports/playbacks share the import cap; other artifact routes share the byte cap.
This is one-controller pilot enforcement, not a distributed subscription ledger.

## Known limits

Candidate evidence is captured in the customer's trusted CI environment; this server
does not rerun their agent. Checksums, task identity and approved-fixture matching
detect changed/missing evidence, but do not attest that CI told the truth. Native
unrestricted production capture is not a sandbox. Controlled model rerun, general
coding/browser frameworks, self-service signup, invites, scoped keys, hosted tenant
API, billing and product UI lifecycle screens are deferred. No production deployment
or live commercial transactions occur in this change. Original static Vercel hosting
stays on its last authorized build.
