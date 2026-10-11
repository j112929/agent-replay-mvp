# Agent Regression Guard requirements

Tagline: **Turn production agent failures into regression tests that run
automatically in CI.** Buyers are CTOs/founding engineers at AI teams of 5–50.
Demand and willingness to pay are unvalidated. The underlying platform remains
Agent Benchmark, Evaluation, Replay & Regression infrastructure.

## First slice acceptance

| Boundary | Required evidence |
| --- | --- |
| Import | native/generic/OTLP normalized v2, safe relationship validation and canary redaction |
| Generate | original failure, explicit expected behavior, recorded fixtures and existing case format |
| Validate | supplied positive passes and source negative fails; missing/ambiguous evidence stays incomplete |
| Approve | explicit reviewer, exact immutable version digest and current evidence |
| Execute | existing evidence evaluator; no arbitrary code/network/tool execution |
| Compare | baseline/candidate classifications, changed tool arguments, limitations and versions |
| Gate | PASS/FAIL/INCONCLUSIVE, missing required evidence blocks, correct exit status |
| Isolation | API-derived tenant roots; another organization's cases/traces/results inaccessible |
| Cost | bounded payloads/artifacts/executions/steps, zero model calls |

Initial deterministic verifiers: exact outputs, count/tool selection, causal ordered
calls, forbidden operations, restricted JSON schema and predicates. No generated
code or LLM judge can become an authoritative gate. Native regression commands and
benchmarks must continue to work. Generated cases cannot bypass approval through
legacy commands.

## Later commercial acceptance

Three real teams import failures, approve useful tests and run them repeatedly in
CI without custom branches. Two paid continuations, measured reduced manual work,
low false positives and manageable support are business validation goals. All
remain unverified until real evidence exists.

Self-service signup, org/project/invite management, scoped keys, hosted storage,
billing/entitlements, integrations/usage/billing UI, alerts and restore drills are
separate work. Under-30-minute onboarding is a target, not a measured result. Do not
label the technical slice as a complete commercial SaaS.
