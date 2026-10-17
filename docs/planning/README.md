# Optional predictive planning

This extension preserves Agent Regression Guard and its benchmark/replay/regression
control plane. It is an opt-in experiment, not a replacement agent framework.
[Repository audit and file plan](../plans/predictive-planning-extension.md).

## Offline demonstration

```sh
python -m pip install -e '.[dev]'
agent-replay planning demo --output .replay/planning-demo --tasks 20 --seed 0
```

Use a fresh output directory. This collects real **in-memory** historical
transitions on 60 separate training task groups, then runs the same 20 tasks in
`baseline`, `planning`, and `planning_with_replay_context` modes. Each mode emits
existing benchmark run/results, v2 trajectories, JSON/HTML/JUnit reports and
associated `planning/sessions/*.json`. `experiment.json` includes paired task
classifications, latency/tool/cost metrics, held-out prediction comparison and a
three-state release gate. It includes failures and regressions; success is not
assigned by the planner or the benchmark runner.

The goal is to deploy a service. Public actions are `deploy`, `run_tests`, and
`check_compatibility`. The real environment independently defines hidden test and
compatibility requirements. Checking consumes an action; some tasks have only two
actions. The default mock predictor knows test consequences but misses some
compatibility requirements. Historical retrieval can help, or cause extra checks
and exhaust the budget. Baseline sometimes succeeds immediately. World models
cannot access private task requirements or the execution adapter.

```sh
agent-replay benchmark run --benchmark predictive-deploy --mode baseline \
  --tasks 20 --seed 0 --output .replay/deploy-baseline
agent-replay benchmark run --benchmark predictive-deploy --mode planning \
  --planning-config examples/benchmarks/predictive-deploy/planning.json \
  --tasks 20 --seed 0 --output .replay/deploy-planning
agent-replay benchmark run --benchmark predictive-deploy --mode planning_with_replay_context \
  --replay-dataset .replay/planning-demo/transitions.json \
  --tasks 20 --seed 0 --output .replay/deploy-retrieval
agent-replay benchmark compare .replay/deploy-baseline .replay/deploy-planning
agent-replay benchmark report .replay/deploy-planning --baseline .replay/deploy-baseline \
  --policy examples/benchmarks/predictive-deploy/gate-policy.json
agent-replay benchmark gate --baseline .replay/deploy-baseline --candidate .replay/deploy-planning \
  --policy examples/benchmarks/predictive-deploy/gate-policy.json --output .replay/deploy-gate.json
```

Gate exit codes with a release policy: 0 nonblocking, 1 blocking FAIL, 3 blocking
INCONCLUSIVE, 2 invalid request. A planning demo command returns 0 if the experiment
completed, even when its embedded example gate fails. CI tests that distinction.
The example cost policy is intentionally strict; it is not a recommended universal
production threshold. Missing metrics, repeated groups or insufficient samples
produce inconclusive evidence rather than a claimed statistical pass.

## SDK integration

```python
from agent_replay.planning import PlanningConfig, Planner, PlanningAgent, CandidateGenerator
from agent_replay.planning.benchmark import DeploymentBaseline, SCHEMAS, policy
from agent_replay.planning.models import MockWorldModel

planner = Planner(CandidateGenerator(policy, SCHEMAS), MockWorldModel(),
    PlanningConfig(enabled=True, candidates=3, horizon=3, rollouts_per_candidate=3))
agent = PlanningAgent(DeploymentBaseline(), planner)
# Implements existing async BenchmarkAgent.run(task, environment, capture).
# Pass to existing run_benchmark with an explicit trusted execution adapter.
```

Custom adapters expose a sanitized public `snapshot()`, `authorize(action,
expected_version)` and `execute_action(action)`. The executor must enforce its own
permissions and atomic version check. The bundled adapter is in-memory only.
There is deliberately no generic shell/production execution adapter. Existing
local terminal benchmarks run unchanged and reject unsupported planning modes.
Disabled planning delegates directly to the baseline without planning metadata.

Generator validates existing declarative tool schemas and deduplicates canonical
JSON actions; semantic equivalence beyond canonical arguments is adapter-specific.
MPC simulates H steps × N samples, scores final reward minus cost/failure risk/
accumulated uncertainty, executes only the selected **first** action, then replans
from the real observation. Schema/selection/state errors default to abort. An
explicit `fallback="baseline"` requires a baseline `choose_action` and still passes
through the same schema and authorization boundary. Model errors never grant
execution authority. `Planner.rollout` also exposes bounded imagination directly.

Defaults: disabled, K=3, H=3, N=3, 10,000 ms, 12,000 tokens, 128 prediction calls,
6 real actions. There are finite upper bounds and 64 KiB payload / 128 KiB session
planning / 512 KiB per-trajectory planning-record budgets. Termination reasons are
persisted. Async callbacks must cooperate with cancellation; a blocking custom
Python callback cannot be safely preempted by an asyncio timeout. The existing
POSIX benchmark task alarm remains the outer hard deadline. Untrusted code must
not be supplied as callbacks.

## Models and privacy

- `MockWorldModel`: deterministic controlled deployment hypotheses, including
  intentionally incorrect and optimistic variants. Refuses other environment versions.
- `ReplayWorldModel`: same-version historical action/state retrieval, excludes
  current task group; stores dataset digest and source trace/step references.
  Empirical frequencies are **uncalibrated**. Optional explicit fallback is recorded
  in individual prediction provenance.
- `LLMWorldModel`: optional SDK-only provider-registry adapter, no tool capability.
  Uses an isolated fixed Python transport process, bounded wall/output/CPU/memory,
  allowlisted configured credential environment and conservative token reservation.
  This is trusted provider I/O, **not** a sandbox for arbitrary customer code.
  Hosted simulation and CI cannot invoke it. Live provider quality/cost was not tested.

Sanitization precedes persistence, and redacted action fixtures are refused rather
than reconstructed. Provider context is sanitized; model requests may still contain
business data and require an explicitly configured appropriate provider. The model
never receives the executor. Numeric model confidence cannot self-certify calibration.
Unknown usage/cost remains unknown. A canceled request may already be billed by a
provider; local termination cannot undo that request. No online weight updates.

## API and replay UI

Python controller: authenticated `POST /api/v1/planning/simulate`, then
`GET /api/v1/planning/sessions/{session_id}` and `/candidates`, `/rollouts`, `/outcomes`.
Local POST requires the existing session token; organization mode uses existing
Bearer identity/roles and tenant artifacts. SaaS uses the same suffix under
`/api/saas/projects/{project_id}/planning`, with scoped keys/session auth,
idempotency, monthly run quota and audit. Example body:

```json
{"context":{"task_id":"demo","task_group":"demo","goal":"Deploy",
"state":{"tests_run":false,"compat_checked":false,"remaining_steps":6},
"state_version":0,"environment_version":"deployment-memory.v1"},
"config":{"enabled":true,"horizon":3,"rollouts_per_candidate":3}}
```

Hosted simulation is bundled mock-only, H≤3, N≤3, calls≤32 and ≤10 seconds; it
performs **no real execution**, accepts no provider URL/code. It persists only
planning hypotheses, with `real_execution: null`. Cross-tenant reads are denied.
Existing project/controller artifact retention includes associated planning files.

Import a benchmark trajectory JSON into the existing debugger, or the connected
workspace Failures view. Inspect → Predictive planning decisions shows candidates,
scores, model/configuration provenance, nested hypothetical futures and predicted
versus actual observations. Filter mismatches/denied decisions. Existing data-only
replay preserves all planning metadata and never invokes imagined or real tools.
There is no new trajectory step kind, SQL migration, or schema-version bump;
`planning-session.v1` is associated optional metadata. Legacy v2 readers still work.

## Offline datasets, calibration and promotion

```sh
agent-replay planning dataset .replay/planning-demo/planning --output .replay/transitions.json
agent-replay planning show .replay/planning-demo/planning/planning/sessions/SESSION_ID.json
```

`dataset.extract` only uses completed real structured tool steps with action,
state and environment provenance. Replay artifacts, hypotheses and redacted
fixtures are excluded. `split` groups by task; `assert_disjoint` rejects overlapping
groups, trace IDs and exact normalized contexts. Semantic near-duplicate grouping
remains the data producer's responsibility; there is no semantic leakage classifier.

`metrics.calibration` computes Brier/ECE, reliability bins, binary ROC-AUC and PR
average precision, risk/coverage and bootstrap intervals only from explicit binary
outcomes/probabilities. One-class AUC and missing probabilities remain null.
`calibration.fit` uses ≥20 independent labeled training groups and both classes;
`promote` requires distinct train/validation/test provenance and significant paired
held-out Brier improvement. Promotion writes an offline artifact; it does not
change deployed models or model weights. No automatic production promotion.

Bootstrap intervals use 400 seeded resamples and assume independent supplied task
groups; they are exploratory, not a certification. Final task forecasts, not
imagined samples, supply labels. Repeated stochastic trials should be grouped
before inference; pooled repeated groups are rejected by planning release checks.
The bundled benchmark is deterministic. Arbitrary stochastic-environment adapters
and cross-seed grouped inference are not implemented.

## Verification and limitations

[Measured seed-0 results](results.md). The new GitHub Actions workflow runs the
offline experiment, verifies paired evidence and the expected budget failure, and
uploads reports. Existing benchmark, security, regression, rollout, SaaS and UI
suites remain required. No model provider, payment, production tool, GPU or new
queue/database dependency is required. No production reliability gain is claimed.
