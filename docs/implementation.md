# Agent Regression Debugger 0.2

The implemented product path is capture → controlled full rerun → structural comparison → business assertions → CI. The old single-step SDK and web debugger remain available. Public package/CLI names are unchanged.

## Evidence and reproducibility

`capture(..., input=task, durability="sync")` opts into v2 files and journals. Legacy `capture(name)` continues writing v1 for compatibility. The in-memory `Capture.trace` retains the compatible representation; `Capture.path` points to the v2 file for new captures. v1 import is non-destructive; it does not invent missing run inputs or environment history.

A journal is one JSON record per boundary start/finish plus run start/output/failure/finish. Buffered writes flush at each boundary, with fsync on failure and finish. Sync writes fsync every record. `recover` reads a valid prefix and marks an unfinished run interrupted. An unfinished external write has unknown effects; recovery does not execute it again. Default capture storage failures warn; use `on_capture_error="raise"` in validation environments.

Use `run.set_output` for the task result and `run.fail` for business violations even when execution completed. Credentials are filtered before persistence; user redactors are supported. Free text is not guaranteed PII-free. Redacted/truncated inputs and outputs cannot become strict frozen matches.

Bundles contain a checksummed trace and manifest. They intentionally do not include executable code or credentials. Copy the trusted project and its prepared environment separately. The importer validates all content before writing and rejects unexpected archive entries, symlinks, excessive sizes, and checksum mismatches.

## Project registry

Create `agent-replay.json` in the trusted project:

```json
{
  "schema_version": "1.0",
  "project_id": "refund-agent",
  "entrypoints": {"refund": {"callable": "my_app:agent"}},
  "providers": {
    "openai": {"kind": "openai_compatible", "api_key_env": "OPENAI_API_KEY"},
    "anthropic": {"kind": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"}
  },
  "tools": {"lookup-v2": {"callable": "my_tools:lookup_v2"}},
  "error_mappings": {"ValueError": "ValueError"},
  "suites": {"critical": ["tests/agent_cases/refund.case.json"]}
}
```

The entrypoint receives `run`; task input is `run.input`. Project paths are constrained to the project root. Imported trace/bundle data never selects a Python import. Entrypoint, provider, tool, and state adapter IDs must come from this explicit configuration. This runs trusted code; a worker subprocess is not a sandbox.

`run.clock` and `run.random` can be supplied through `determinism.clock_fixture` and `rng_seed`; they do not monkeypatch unrelated libraries. `state_adapter_id` invokes a registered factory with the task input and exposes its returned object as `run.state`. A dependency lock digest identifies the environment but does not reconstruct it.

## Replay specification

```json
{
  "entrypoint_id": "refund",
  "scope": "agent",
  "policy": "frozen",
  "limits": {"timeout_seconds": 60, "max_steps": 100, "max_model_calls": 0},
  "fixtures": [{
    "id": "refund-test-double",
    "selector": {"boundary_id": "payments.refund"},
    "consume": "repeat",
    "expected_calls": 1,
    "response": {"kind": "return", "value": {"ok": true, "simulated": true}}
  }]
}
```

Rules match explicit boundary ID, parent scope, optional lane, and occurrence. Calls without a unique recording stop; no automatic live fallback. Fixtures support once, sequence (`responses`), and explicit repeat. New tool parameters require an explicit fixture or local implementation rule. A fixture changing a tool result does not itself establish task correctness.

Cross-model rules use `model_rules: [{"selector":{"name":"chat.completions"},"mode":"live","provider_id":"anthropic","model":"YOUR_MODEL_ID"}]`, and require a positive `limits.max_model_calls`. `limits.max_tokens` bounds each call's requested output tokens. No automatic pricing estimates are made; cost-based budgets are rejected instead of silently ignored. Generic decorated LLM functions remain frozen; model substitution uses `run.chat` or the new `run.model` boundary.

`run.model(request, provider_id=..., project=...)` accepts portable text/tool blocks via `providers.normalize`; `run.chat` retains Chat Completions return semantics. Native Anthropic uses the non-streaming [Messages API](https://platform.claude.com/docs/en/api/messages/create). Unsupported fields, streaming, image and thinking blocks fail explicitly. Tests use local HTTP providers; paid provider smoke tests are not represented as complete.

Tool rules use `tool_rules: [{"selector":{"boundary_id":"orders.lookup"},"mode":"local","implementation_id":"lookup-v2"}]`. The actual candidate inputs go to that registered implementation. Its side effects remain the responsibility of the trusted test environment.

`--commit REF` creates a detached temporary worktree, runs that code in a separate interpreter, and removes the worktree afterward. It never checks out over the user's workspace. Use a registered `environment_profiles` entry with a prepared `interpreter` and select it via `environment_profile`; dependencies are not implicitly installed. The controller SDK is injected from the current installation so a historical application does not silently load an old replay engine.

## Comparison and verdicts

Stable/source boundary keys align steps; additions, removals, and ambiguous groups stay visible. JSON Pointer changes distinguish missing/null/false/zero. Timing is excluded from correctness comparison. Reports show the first observable divergence, not an asserted root cause. Concurrent lanes do not have a global causal order.

Cases support equals/not_equals/exists/lte/gte/contains/count/forbidden/execution_completed/no_unhandled_error. Zero assertions and empty suites are errors. At least one business assertion is mandatory. A missing numeric value fails. An incomplete or ambiguous replay is inconclusive. Count and execution assertions prevent a no-action candidate from passing a refund test.

`case accept` explicitly locks a passing result; `test` never updates baselines. Changed case/fixture digests mark baselines stale. The original production source remains immutable. Test results execute current candidate code, not a successful saved trace.

Exit codes: 0 pass; 1 business failure; 2 configuration/provider/worker error; 3 inconclusive; 130 suite cancellation. Legacy single-step replay retains 0/1/2. JUnit marks inconclusive skipped while the CLI remains nonzero. JSON and HTML include the evidence. Live cases require `test --live` plus `live_policy.repetitions` and `min_pass_rate`; provider failures are not removed from the denominator.

## Interfaces

`inspect`, `doctor`, `bundle export/import`, `recover`, `run`, `experiment`, `compare`, `case create/accept`, `test`, `cleanup`, `demo`, `serve` are available through the same CLI. `experiment --variants variants.json` accepts an explicit list and runs serially to keep provider use bounded. JSON output is stdout; diagnostics are stderr.

The local `/api/v1` service offers health, trace import/list/detail, failures, doctor, experiments/jobs/cancel, comparisons, cases, and registered suites. Mutations require a same-origin session token. A single worker and persisted idempotency keys avoid duplicate model runs. Restarted running jobs become interrupted, not retried. The API intentionally does not accept arbitrary filesystem paths, code refs, or import expressions. CLI handles those trusted local operations.

The static Vercel site provides import, comparison, case draft export, and report viewing. It does not run Python or store provider keys. Real execution requires the loopback runner. Source ZIP, wheel UI, and plugin vendor all come from one source tree; `scripts/package_release.py --check` catches drift.

## Boundaries

This is explicit Python instrumentation, from-start reruns, and local CI. It is not distributed tracing, process checkpointing, a remote sandbox, hosted ingestion, a billing service, or automatic instrumentation for every framework. Large artifacts remain limited to the documented import limits. Production overhead and real design-partner adoption need measurement in their environments; synthetic tests do not establish production readiness.

## Validation record for this release

Local Python 3.14: 41 SDK/API/contract tests passed; Node: 10 tests passed. Browser verification exercised failure inspection → full fixed-agent rerun → argument diff (29900 to 2900) → case creation → passing suite. Wheel and self-contained plugin each ran the real offline fail/pass/fail story. Twenty consecutive frozen demo reruns produced identical normalized boundary evidence. A 2,000-boundary comparison took approximately 0.76 seconds on the development machine; this is not a production benchmark. CI additionally covers Python 3.10/3.12/3.14 and distribution checks.
