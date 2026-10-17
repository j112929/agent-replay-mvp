# Agent Regression Guard

**Turn production agent failures into regression tests that run automatically in CI.**

Built incrementally on the Agent Benchmark, Evaluation, Replay & Regression Platform.
The first commercial prototype imports sanitized native/generic/OTLP JSON, generates
recorded fixtures and declarative verifiers, validates them on positive/negative
examples, requires explicit approval, and evaluates candidate evidence with CI gates.

Release protection now has an explicit trusted-evidence path: an administrator pins
the commit, approved case/fixtures, configuration and budget before a fresh CI
capture; a project-scoped signer authenticates that run; the release gate rejects
stale, tampered or reused evidence. Ordinary captured-record evaluation remains
available and is explicitly **not release eligible**. See the
[CLI/API setup and trust boundary](docs/productization/trusted-release-evidence.md).

```sh
pip install -e .
agent-replay guard demo --directory .replay/guard-demo
```

The offline synthetic coding A/B demo returns **PASS → FAIL → INCONCLUSIVE**,
with zero model calls or real tool operations and no persisted synthetic token.
It reuses existing trajectory v2, assertions, comparison and JSON/HTML/JUnit reports.
Recorded playback is inspection; fresh candidate captures are evaluated separately.

[End-to-end guide](docs/productization/vertical-slice.md) ·
[Repository assessment](docs/productization/current-state-assessment.md) ·
[Commercial MVP plan](docs/productization/commercial-mvp-plan.md) ·
[Architecture decisions](docs/productization/architecture-decisions.md)

## Self-service product workspace

The connected workspace adds registration/login, email verification and password
reset, organizations/projects/invites, roles, scoped revocable API keys, monthly
usage quotas and Stripe **test** Checkout/Portal with signed webhook entitlements.
Seven product views cover overview, failures, regression tests, test runs,
integrations, team/projects and billing. Existing SDK and benchmark commands remain.

```sh
python -m pip install -e '.[dev]'
npm ci
cp saas/.env.example .env
# Generate and privately set BETTER_AUTH_SECRET in .env; configure SMTP as needed.
npm run start:env
```

Open `http://127.0.0.1:3000/debugger.html`, register, create a project and choose
**Failures → Load synthetic A/B demo**. Generate, review and approve a test, then
compare A/B: **FAIL**, zero external tool operations. Integrations provides CI keys
and a GitHub Actions configuration. [Setup, API and demo](docs/productization/self-service.md)
· [Controller operations](docs/operations/saas-runbook.md).

**Verified scope:** offline/local single-controller implementation; a persistent
Linux host is required for SQLite and artifacts. Existing Vercel hosting remains
static and falls back to the prior debugger. No hosted customer service, live
billing, real Stripe transaction or SMTP delivery is claimed. Prices remain
hypotheses, and no customers or product-market fit are claimed. Live keys/events
are rejected until separately authorized and reviewed.

## Agent Benchmark, Evaluation, Replay & Regression Platform

Safely reproduce production agent failures from sanitized recorded evidence,
compare versions, detect regressions and enforce CI release gates—without
re-executing dangerous real-world operations during default replay.

## v0.5 model-based planning release candidate

The existing planning layer now supports shadow/active modes, bounded prediction-error
correction, an independent Refund oracle/evaluator, authorized JSONL datasets, actual
CPU supervised fitting and an optional local HF/LoRA path. Static planning views
inspect imported evidence; they do not execute hosted agents or training.

```sh
agent-replay planning benchmark --output .replay/v05 --tasks 20
```

Controlled Refund results: baseline/shadow 8/20, active 16/20, correcting 20/20.
A safe-rule policy without a world model also passes 16/20, so incremental world-model benefit is not established. Correction consumes extra calls and cost; this is synthetic evidence, not a production
reliability claim. CPU LoRA smoke verifies real gradients/checkpoints, not useful
prediction quality. [Audit](docs/v0.5-repository-audit.md) ·
[Setup, safety, training and limitations](docs/v0.5-world-model.md) ·
[Release notes](docs/v0.5-release-notes.md).

## Optional predictive planning experiments

Generate candidate actions, simulate bounded hypothetical futures, score reward,
risk and uncertainty, authorize only the selected real action, then compare
predictions against actual observations. Planning is disabled by default and never
executes tools inside simulations. Historical replay transitions support offline
retrieval/calibration; existing benchmark reports and CI gates compare all modes.

```sh
agent-replay planning demo --output .replay/planning-demo --tasks 20 --seed 0
```

The controlled seed-0 experiment measures 5/20 → 10/20 → 11/20 task passes,
including a new failure in the retrieval mode and increased cost/tool calls.
Prediction improvement remains statistically inconclusive; the example cost gate
blocks the candidate. These are synthetic results, not production reliability claims.
[Setup, SDK/API, replay and limitations](docs/planning/README.md) ·
[Measured results](docs/planning/results.md) ·
[Audit and migration plan](docs/plans/predictive-planning-extension.md).

## Security pilot workflow

```sh
pip install -e .
agent-replay security-demo --directory .replay/security-demo
```

The offline demo captures simulated Model A/B behavior, redacts a synthetic API
token, intercepts a simulated payment, detects the critical-task regression,
plays back recorded observations and writes a **FAIL** production gate decision.
No real provider/payment operation runs. Open
`.replay/security-demo/candidate/reports/index.html` for evidence.

```sh
agent-replay benchmark run --benchmark local \
  --suite examples/benchmarks/terminal-smoke --agent demo \
  --output .replay/benchmarks/baseline
agent-replay benchmark run --benchmark local \
  --suite examples/benchmarks/terminal-smoke --agent demo-v2 \
  --output .replay/benchmarks/candidate
agent-replay benchmark compare .replay/benchmarks/baseline .replay/benchmarks/candidate
agent-replay benchmark report .replay/benchmarks/candidate --baseline .replay/benchmarks/baseline
```

Expected smoke outcome: 2/4 → 3/4, with two new passes and one new failure.
Use fresh output directories. [Benchmark guide](docs/benchmarks.md).

**Security compatibility change:** host-based agent/tool/model replay execution
is now denied. `MOCK_REPLAY / TRACE_PLAYBACK` is supported. SANDBOX_REPLAY and
MODEL_RERUN are disabled until a verified isolation backend exists. Legacy examples
below that request executable reruns are historical; see the migration guide.
Normal capture, benchmark agents and training are real trusted execution, not
sandboxed replay. This is a pilot security foundation, not enterprise certification.

- [Repository assessment and file-level plan](docs/architecture/security-gap-analysis.md)
- [Security architecture and organization roles](docs/security/security-architecture.md)
- [Sanitization data flow](docs/security/data-flow.md) · [Threat model](docs/security/threat-model.md)
- [Replay isolation / breaking changes](docs/security/replay-isolation.md)
- [PASS / FAIL / INCONCLUSIVE gates](docs/security/release-gates.md)
- [Retention and backup limits](docs/security/data-retention.md)

## Existing rollout and replay capabilities

### Distributed RL Rollout & Replay Infrastructure

Ray / process Actor workers → versioned trajectories → independent rewards → replay/debug → real policy updates. Includes a CPU-only REINFORCE demo and preserves the Agent Regression Debugger.

```sh
pip install -e .
agent-replay rollout demo --actors 3 --rounds 8
```

New in v0.4: [DDP/GRPO, vLLM, async scheduling, storage, recovery and benchmark guide](docs/distributed-training.md). GPU execution and Kubernetes deployment remain unvalidated without a GPU environment.

See [rollout architecture, API, workers and scope](docs/rollout-infrastructure.md). The Vercel site displays recorded/imported evidence; the controller and workers run on your infrastructure. This release uses one SQLite controller. Ray GPU reservations and a GRPO learner are implemented; GPU execution and controller high availability are not validated.

---

# Replay — Agent Regression Debugger

[Live workspace](https://agent-replay-debugger.vercel.app) · [Implementation guide](docs/implementation.md) · [Original implementation plan](docs/agent-regression-debugger-plan.md)

**Turn a production agent failure into a regression test.**

Capture the input and model/tool boundaries, compare evidence and verify business assertions in CI. Python 3.10+, MIT, no runtime dependencies. Package version 0.5.0; the package has not been published to PyPI. Executable replay is disabled; trusted CI captures candidate executions separately.

## Hosted workspace: 中文 / English

[Overview](https://agent-replay-debugger.vercel.app/) · [Replay & regression](https://agent-replay-debugger.vercel.app/debugger.html) · [Benchmarks](https://agent-replay-debugger.vercel.app/benchmark.html) · [Refund evaluation](https://agent-replay-debugger.vercel.app/refund/evaluation.html)

The four pages share grouped navigation and a persistent language preference. Overview, Replay and Benchmarks translate product copy, controls, dialogs and known validation messages. Refund offers Chinese and English aggregate reports. Switching language preserves the active Replay view, selected captures, imported snapshots/reports, and open dialog drafts. Language preference is browser-local; imported evidence is not uploaded by the static viewer.

中文：四个页面共用分组导航和语言选择。首页、Replay、Benchmarks 的正文、按钮、弹窗和已知校验提示支持中英切换；Refund 提供双语汇总报告。切换语言不会清空当前视图、选中记录、导入数据或弹窗草稿。语言偏好保存在当前浏览器，静态查看器不会上传导入的执行证据。

Raw JSON, code, trajectory names, identifiers, hardware/provider diagnostics and execution evidence remain literal. Unknown service errors have a translated explanation followed by unchanged diagnostic details. The legacy step debugger at `/legacy.html` retains its original interface and is outside the four-page bilingual workspace.

### Refund live evaluation

The hosted report contains **12 synthetic task families, 24 executable scenarios and 72 evaluated records**, split by related provenance groups. Validation and test each contain 2 scenarios. Qualified verifier evidence and complete model executions remain local.

Two pinned OpenAI models were evaluated with 3 repetitions per scenario, producing 24 real responses and no provider errors:

| Model | Validation successes / runs | Test successes / runs |
|---|---:|---:|
| `gpt-4.1-mini-2025-04-14` | 6/6 | 0/6 |
| `gpt-4o-mini-2024-07-18` | 0/6 | 0/6 |

Stable cross-model failure reproduction was **2/4 scenarios (50%)**. Eleven of the eighteen failed runs violated the plain-JSON protocol through Markdown code fences; JSON response mode and automatic fence stripping were not enabled. These results measure end-to-end behavior on four synthetic held-out scenarios, not production generalization, an intrinsic model ranking or RL training gains.

Only the static website and aggregate reports are publicly deployed. **Source and full dataset ZIP files remain local**, with no public download endpoints. No model API key or complete trajectory dataset is published with the website.

## A working failure → fix → regression story

```bash
python -m pip install -e .
agent-replay demo --directory .replay/demo
agent-replay serve --project .replay/demo/agent-replay.json
```

Open **http://127.0.0.1:8765**. The demo actually runs an agent that refunds 299.00, a fixed agent that refunds 29.00, then the original bug again. Assertions require one refund, the correct order, completed execution, and at most 30.00. The results are **fail → pass → fail**. All payments and model responses in the demo are synthetic; there are no external calls.

The UI provides Failures, paired boundary comparison, Regression CI results, and setup. The hosted site works with imported JSON and exported reports; local execution needs the Python runner. The previous single-step debugger remains at `/legacy.html`.

## Capture a production boundary

```python
from agent_replay import capture, tool

@tool(boundary_id="orders.lookup", side_effect="read")
def lookup(order_id):
    return your_lookup(order_id)

with capture("refund-agent", input={"order_id": "1042"}, durability="sync") as run:
    result = your_agent(run)
    run.set_output(result)
    if result["amount_minor"] > 3000:
        run.fail("Refund exceeds approval", category="assertion")
```

A completed execution can still violate a business rule. v2 captures persist journals at boundaries; `recover` reads a valid prefix after interruption. v1 capture and the original `replay_step`/`replay_agent` SDK APIs remain compatible. New `async_replay_agent` is awaitable in an active event loop.

## Historical rerun interface and current comparison

The executable rerun commands below describe the historical debugger and are
disabled by the current security controller. Use the Guard workflow for captured
candidate evaluation, and the trusted-evidence release path for release decisions.
Recorded inspection and comparison remain available. The
[configuration guide](docs/implementation.md) retains the historical API details.

```bash
agent-replay inspect failure.json
agent-replay doctor failure.json --project agent-replay.json --entrypoint refund
agent-replay run failure.json --project agent-replay.json --spec replay-spec.json
agent-replay compare failure.json candidate.json --output comparison.json
agent-replay case create failure.json --entrypoint refund --output tests/agent_cases/refund.case.json
# Add reviewed business assertions and explicit fixtures to the draft.
agent-replay test tests/agent_cases --project agent-replay.json --report-dir .replay/reports
```

Reports include JSON, JUnit XML, and standalone HTML. Default CI has no live model calls. A recorded single step cannot make a regression suite pass. Changed/ambiguous inputs stop instead of silently executing real tools. Baselines are accepted explicitly; the original failure is preserved.

Cross-model experiments support non-streaming OpenAI-compatible Chat Completions and native Anthropic text/tool Messages through registered providers. Tool replacements use registered local implementations. `run --commit REF` actually executes a separate detached worktree with a prepared environment. Live models remain nondeterministic and are opt-in; environment/coverage gaps stay visible.

## Guarantees and limits

- Explicit instrumented boundaries, not automatic capture of every library call.
- From-start execution, not process checkpoint resume. Trusted project code still runs normally.
- No real-tool fallback after a mismatch. Frozen outputs are not proofs of business correctness.
- Checksummed data bundles, best-effort credential redaction, and user redactors. Not complete PII detection.
- Same-origin loopback API with a session token, one worker, persisted jobs and cancellation.
- Static hosted UI does not run Python, receive local traces automatically, or store API keys.
- Provider unit tests use local mock servers. The hosted refund report also includes a small live-model evaluation; production workload overhead and broader provider integration still need environment-specific validation.

## Development and packaging

```bash
python -m pip install -e '.[dev]'
python -m unittest discover -s tests -v
node --test tests/*.test.mjs
python scripts/package_release.py
python scripts/package_release.py --check
```

`dist/` is the canonical web source. Packaging synchronizes `sdk/agent_replay/web`, public schemas, and the self-contained plugin vendor. Do not edit generated copies. The deterministic source ZIP excludes user traces, credentials, environments, and recursive archives. GitHub workflows run SDK/web tests, the fail/pass/fail story, regression reports, and wheel/plugin smoke checks.

Vercel serves `dist/` as a static site using `vercel.json`. Backend execution stays in the local runner.
