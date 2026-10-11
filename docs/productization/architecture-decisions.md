# Architecture decisions

## ADR 001: stack on the tested security foundation

PR #3 contains the required fail-closed sanitizer, safe playback, tenant roots and
benchmark evaluator. Reuse it unchanged except additive guard support. Main does
not contain that work yet. Review this change against PR #3, and land the foundation
first. Do not merge or deploy implicitly during productization.

## ADR 002: JSON artifacts for the first slice

Reuse atomic storage and content hashes. Versioned existing cases and separate
approval records add provenance without duplicating BenchmarkRun or Trajectory.
One local controller and bounded artifact counts suffice for a reproducible demo.
Use an OS file lock for quota-sensitive mutations where supported; fail closed on
unsupported platforms. A hosted multi-instance service needs transactional quotas
and account metadata before release. Do not repurpose training SQLite for billing.

## ADR 003: deterministic verifier, explicit human approval

Generate restricted declarative assertions from an operator-supplied expected output
or rules. Require a known positive trace and the original failed negative trace.
Store schema validation, pair outcomes and content digests. Never infer an expected
output from a failed trace or call generated Python/eval. Validation confidence is
`example_pair_only`, never a probability. Any change invalidates approval.

## ADR 004: playback is not candidate execution

Recorded playback reconstructs historical observations without tools or a model.
Evaluation reuses checksummed candidate trajectories captured in customer-owned CI;
it does not execute customer code in this server. Missing/mismatched evidence is
INCONCLUSIVE. The bundled demo uses trusted, deterministic toy agent code and
data-only mocked tools to produce distinct A/B captures. Arbitrary executable agents
and controlled model reruns remain unavailable until an isolation design is reviewed.

## ADR 005: OTLP JSON file adapter, explicit mapping

Use native and generic adapters plus OTLP JSON resource/scope/span containers.
Do not embed an OTLP collector, exporter SDK, tracing database or proprietary service
client. Preserve causal parents, report missing payloads and partial coverage, and
sanitize decoded attribute key/value pairs. No direct Langfuse/LangSmith/OpenAI
Agents integration is claimed. Data mapping is documented and tested with fixtures.

## ADR 006: narrow release policy and customer-owned CI

Use the existing regression reports/exit conventions. Approved enabled critical
cases fail the gate; missing required evidence blocks as INCONCLUSIVE. This is
deterministic suite protection, not an accuracy leaderboard or repeated-trial
statistical inference. Existing benchmark statistical gates remain available.
Actions uses contents: read, no customer secrets, no pull_request_target, and short
retention of sanitized synthetic artifacts. Repository owners configure required
checks; no branch-protection administration occurs in this slice.

## ADR 007: exact JSON assertions preserve nested value types

The previous assertion engine compared Python containers, where nested `true` and
`1` could compare equal. Exact assertions and predicates now compare canonical JSON;
array membership uses the same comparison. This intentional correctness change
prevents misleading positive verifier evidence. Numeric inequality operators keep
their explicit numeric semantics. Existing offline regression tests still pass.

## Decisions requiring separate approval

No new live billing, production release, hosting migration, remote public tenant
API or untrusted-code execution is authorized by this immediate assessment/slice.
These are deferred, not worked around. Candidate approval is a product action:
the CLI must require explicit reviewer input and HTTP must require ADMIN. The demo
uses an explicitly named synthetic reviewer and does not approve customer cases.

## ADR 008: additive self-service control plane

The later self-service request authorizes implementing accounts, test billing and
the full workspace. Add one Node controller using Better Auth and SQLite; retain
vanilla UI and Python guard code. Existing rollout SQLite is unrelated and is not
repurposed. A fixed JSON worker bridges the established evaluator; no arbitrary
agent code, model rerun or production tool capability enters the SaaS service.

## ADR 009: billing is authoritative and test-only

Use hosted Stripe test Checkout/Portal and the provider SDK's signature verifier.
Retrieve current subscription state for every relevant webhook, serialize
reconciliation and deduplicate event IDs. Reject live keys/events. Central plan
configuration and transactional monthly reservations bound usage. Redirects,
client status and request-selected price IDs never grant entitlements.

## ADR 010: durable single controller and immutable review

Separate account metadata from JSON evidence. Enforce a controller process lock,
server-derived tenant roots, current memberships, project ACLs and hashed scoped
keys. Store small job receipts and pinned suite versions. Case control sidecars
permit explicit rejection/disable without rewriting approved evidence; disabled
required cases block gates as INCONCLUSIVE. Rejected versions require regeneration.
Production hosting requires durable disk/TLS/SMTP; no stateless Vercel deployment
or managed storage migration is claimed by this implementation.
