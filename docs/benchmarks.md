> Security update: executable replay is disabled. See [replay isolation](security/replay-isolation.md) and [release gates](security/release-gates.md). Benchmark execution itself remains trusted real execution.

# Agent benchmark, evaluation and regression control plane

The product is the control plane around agents: run task suites, capture actions,
evaluate outcomes, compare versions, inspect/replay failures, and gate changes.
The bundled demo agent is a synthetic test fixture, not the product or a competitive solver.

## Five-minute offline demo

From the repository root, on Linux or macOS with Python 3.10+:

```sh
pip install -e .
agent-replay benchmark run --benchmark local \
  --suite examples/benchmarks/terminal-smoke --agent demo \
  --output .replay/benchmarks/baseline
agent-replay benchmark run --benchmark local \
  --suite examples/benchmarks/terminal-smoke --agent demo-v2 \
  --output .replay/benchmarks/candidate
agent-replay benchmark compare \
  .replay/benchmarks/baseline .replay/benchmarks/candidate
agent-replay benchmark report .replay/benchmarks/candidate \
  --baseline .replay/benchmarks/baseline
```

Baseline: 2/4 (wrong answer and timeout). Candidate: 3/4. Two tasks improve,
one previously passing task regresses. Overall success rises 25 percentage points,
while `easy` and `filesystem` success fall from 100% to 50%.
Tags are labels; this tiny `long_horizon` fixture is not evidence of real long-horizon ability.
Timing varies by machine; outcomes and task classifications are deterministic.
No models, network calls or credentials are needed. Reuse a new output directory
for each run; existing directories are rejected to preserve prior evidence.

```sh
# Intentionally fails with exit 1: preserve-answer is a new regression.
agent-replay benchmark gate \
  --baseline .replay/benchmarks/baseline --candidate .replay/benchmarks/candidate
# Passes with an explicit tolerance for this demonstration.
agent-replay benchmark gate \
  --baseline .replay/benchmarks/baseline --candidate .replay/benchmarks/candidate \
  --max-success-drop 0.02 --max-new-failures 1 --max-cost-increase 0.25
agent-replay benchmark list
agent-replay benchmark show .replay/benchmarks/candidate --json
```

Thresholds use fractions: `.02` means 2 percentage points of success rate;
`.25` means 25% relative increase in mean cost/task. Equality passes. Default
success drop and new regressions allowed are zero; cost checking is opt-in.
The gate fails closed on missing cost data when cost checking is enabled,
changed task definitions, unequal task sets, empty/incomplete runs, and verifier
or environment errors in either run. No data is represented as null, never zero.
An increase from zero cost to positive cost fails any finite relative cost limit.

`run` returns 0 when execution/evaluation completed, even if tasks failed; 2 means
configuration, persistence, or verifier/environment infrastructure failure.
`gate` returns 0 for pass, 1 for threshold/coverage/measurement failure, and 2 for
invalid input. Use `gate` for CI policy, not `run`'s exit code.
All benchmark commands support `--json`.

## Artifacts and replay

```text
.replay/benchmarks/<run>/
  run.json                 # definitions, suite/verifier digests, agent config, summary
  results.json             # task-level metrics, evaluation and trajectory references
  trajectories/<uuid>.json # existing trajectory v2, not another trajectory format
  journals/<uuid>.jsonl    # existing durable capture journal
  reports/report.json
  reports/index.html
  reports/junit.xml
```

Open `reports/index.html`. Its task table links to trajectory JSON; import these
files into the existing debugger. Or run `agent-replay serve --directory
.replay/benchmarks/baseline/trajectories` and open http://127.0.0.1:8765.
The existing server discovers v2 files recursively under the run directory.
The HTML report does not require a server or external assets.

Replay the timeout step using the existing CLI (exit 1 is expected, since it
preserves the recorded failure):

```sh
python - <<'PY'
import json, subprocess
from pathlib import Path
root = Path('.replay/benchmarks/baseline')
result = next(r for r in json.loads((root/'results.json').read_text())
              if r['task_id'] == 'finish-task')
trace_path = root / result['trajectory_path']
trace = json.loads(trace_path.read_text())
step = next(s for s in trace['steps'] if s['name'] == 'wait')
subprocess.run(['agent-replay', 'inspect', str(trace_path), '--step', step['id']])
result = subprocess.run(['agent-replay', 'replay', str(trace_path), '--step', step['id']])
assert result.returncode == 1
PY
```

Recorded single-step replay reproduces observations; it does not resume a task
or re-evaluate final state. Re-run the benchmark after changing an agent, model,
or prompt, then compare runs. Existing whole-agent replay still requires an
explicit registered entrypoint/project configuration. Benchmark runs do not
implicitly register arbitrary agent code with the replay runtime.

## Local task format and verifier protocol

Only `local` is implemented. A suite contains `tasks.json`, a JSON array of:

```json
{
  "id": "create-file",
  "instruction": "Create result.txt containing exactly 'hello agent'",
  "tags": ["filesystem", "easy"],
  "timeout_seconds": 60,
  "metadata": {}
}
```

Each ID has `<task-id>/verifier.py`. The runner invokes the verifier with the
absolute workspace directory as its first argument, after agent execution:

```python
import json
from pathlib import Path
import sys
p = Path(sys.argv[1]) / 'result.txt'
success = p.is_file() and p.read_text() == 'hello agent'
print(json.dumps({'success': success, 'score': float(success)}))
```

Exit 0 means the verifier completed; its JSON `success` determines task outcome.
Optional fields: `score`, `failure_category`, `metadata`. Nonzero exit, invalid
JSON, invalid result values, or verifier timeout are `verifier_error`, not
`wrong_answer`. Local verifiers have a separate 10-second deadline, configurable
through `LocalAdapter(verifier_timeout=...)`. Task timeout covers setup, agent
execution and final-state collection. Evaluation is skipped on execution failure.
Results retain the phase and exception information. Cleanup failures are
`environment_error` and retain the prior evaluation in metadata.

Every task gets a fresh temporary workspace, removed after evaluation. File tools
reject traversal and resolved symlink escapes. `/workspace/...` is a virtual file-tool
prefix; shell commands must use paths relative to their cwd. It is not a mounted
container path. Shell and Python verifiers are trusted code, run with the runner's
OS permissions/environment, and are **not sandboxed**. Use an external container
for untrusted tasks or model-generated commands. This milestone provides no Docker,
network or OS isolation. The POSIX main-thread timer bounds ordinary blocking code;
it is not protection against code deliberately overriding/catching deadlines.
Shell process groups are killed on timeout and after commands finish.

The demo agents read explicit `metadata.demo_actions.demo` / `demo-v2` scripts.
They cannot solve arbitrary new task instructions. Supported scripted operations:
`write_file`, `read_file`, `list_files`, `execute`, `wait`.

## Adapter and agent extension points

`BenchmarkAdapter` defines `load_tasks`, `prepare_environment(task, capture)`,
`final_state(task, environment)`, `evaluate(task, trajectory, final_state)` and
`cleanup`. Environment/final-state objects are adapter-owned and can have different
internals. `cleanup` is called after every task, including failed setup.

`BenchmarkAgent.run(task, environment, capture)` is async. Python integrations
can pass their own adapter/agent and `AgentConfig` to `run_benchmark`; the CLI
intentionally only registers the implemented local adapter and bundled agents.
Task tools use existing Capture spans, preserving inputs, outputs, errors,
timing and parent IDs. The runner adds `metadata.benchmark` and `evaluation` to
v2 trajectories; runtime/replay/regression/rollout formats remain unchanged.

Run metadata records normalized task definitions, verifier hashes, suite digest,
agent class/config, Python/platform and timestamps. Trajectories include existing
code provenance. Keep the corresponding source commit and verifier dependencies
for reproducibility; hashes alone cannot restore external state. Local v1 verifiers
should be self-contained; arbitrary imported dependency trees are not snapshotted.

## Metrics and comparison semantics

Each result records success, optional score, duration_ms, steps, tool/model counts,
input/output/total tokens (`tokens`), estimated cost (`cost_usd`), retry_count,
failure category, tags and trajectory ID/path. Task duration includes setup,
agent, evaluation and cleanup. Step count includes the agent span and tool/LLM
spans; tool/model counts are separate. Percentiles use nearest rank; medians use
arithmetic middle values. Tag groups overlap and should not be summed.

Unknown model usage or prices remain null. Demo tasks use no LLM tokens and cost
zero. Cost is not inferred from changing provider price lists; adapters can supply
validated cost estimates. Distributions include measured count and missing count,
mean, median, p90, p95 and known-value total. Partial-coverage means are descriptive;
the cost gate requires complete measurements. Retry count derives from capture
attempt numbers; the bundled agents do not automatically retry.

Comparison aligns equal task IDs with identical task definitions/verifier hashes.
Added, removed and changed tasks are reported separately and excluded from matched
metric deltas. All-task summaries are also retained. A new failure cannot be hidden
by aggregate improvement. Task classifications are improvement, regression,
unchanged_pass and unchanged_fail, with named ID lists `newly_passing` and
`newly_failing`. Tag comparisons use the same matched cohort.

Failure taxonomy: wrong_answer, tool_error, tool_loop, timeout, environment_error,
policy_violation, agent_error, verifier_error, unknown. Infrastructure errors remain
in the success-rate denominator and are distinct JUnit `<error>` entries.

## Optional model agent (experimental)

```sh
# OPENAI_API_KEY / OPENAI_BASE_URL come from your environment.
agent-replay benchmark run --benchmark local \
  --suite examples/benchmarks/terminal-smoke --agent openai --model MODEL_ID \
  --max-model-calls 20 --output .replay/benchmarks/live
```

This is a small Chat Completions tool loop using the existing provider transport,
not a sophisticated coding agent. It requires tool-call support. It is covered
with a mocked provider test, not validated against a paid external provider.
The demo's short timeout task is intentionally suitable for scripted tests; create
a suite with realistic timeouts for live models. Provider costs are unknown by
default. Core runner logic contains no model-provider routing.

## Status and roadmap

| Status | Scope |
| --- | --- |
| Implemented | Local adapter, offline demo agents, v2 capture, task verifiers, metrics, task/tag comparison, CI gate, JSON/HTML/JUnit, recorded failure replay |
| Experimental | Optional OpenAI-compatible tool-loop agent; existing distributed-training/GPU experiments retain their documented validation limits |
| Planned | Terminal-Bench, SWE-bench, tau-bench, OSWorld, WebArena and custom internal benchmark adapters |
| Planned | Trajectory-prefix success prediction: P(task_success \| trajectory[0:t]); Brier score, ECE, AUROC, AUPRC, early-warning lead time |

No external benchmark integration or ML predictor is claimed here. Result `metadata`
can carry namespaced future prediction annotations; measured success remains the
verifier's outcome. No ML dependencies, training changes, distributed benchmark
execution, new database or frontend rewrite are part of this milestone.

`.github/workflows/benchmark-smoke.yml` runs offline tests and both agents, saves
reports and comparisons, verifies strict-gate rejection, then applies the explicit
demo tolerance. CI needs no API keys.
