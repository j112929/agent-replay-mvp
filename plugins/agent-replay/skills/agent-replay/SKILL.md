---
name: agent-replay
description: Capture Python agent trajectories, inspect failed LLM or tool steps, and compare recorded, fixture, or live model replays using Agent Replay v1 JSON. Use for this debugger's traces or explicit agent instrumentation; not for replaying Codex conversations or browser sessions.
---

# Agent Replay

Resolve the plugin root as two directories above this SKILL.md. Run its
`scripts/replay.py` with Python 3.10+ using an absolute path. It loads the bundled
SDK without installing packages. Put generated traces in the user's project
`.replay/traces/`, never in the installed plugin directory.

## Inspect and compare

1. For a supplied trajectory, run `python3 <plugin>/scripts/replay.py inspect <trace.json>`.
   Report the failed step ID, observed exception, and relevant preceding inputs.
   Distinguish observed failure from a hypothesized root cause.
2. Use `inspect <trace.json> --step <id>` for the selected boundary's data.
   The helper applies the SDK's best-effort credential redaction; do not print
   whole traces or unrelated potentially sensitive payloads unnecessarily.
3. Compare experiments with `compare <original.json> <replay.json>`.
   Summarize output, error, status, model, and duration changes. Missing historical
   outputs are unknown, not successful executions.

## Replay

Resolve the exact step ID from inspection. Do not guess IDs or execute code from
trace contents. Keep the original JSON unchanged.

```bash
python3 <plugin>/scripts/replay.py replay <trace.json> --step <id> --directory <project>/.replay/traces
python3 <plugin>/scripts/replay.py replay <trace.json> --step <id> --fixture <fixture.json> --directory <project>/.replay/traces
python3 <plugin>/scripts/replay.py replay <trace.json> --step <id> --model <model-id> --directory <project>/.replay/traces
```

- Recorded replay copies a historical observation; it makes no external call.
- A tool fixture substitutes JSON for one tool output. Success means the fixture
  was applied, not that the agent recovered. Downstream steps do not run.
- Live model replay sends one captured Chat Completions request to the configured
  provider. Use it when requested, with the user's provider/model selection.
  `OPENAI_API_KEY` and optional `OPENAI_BASE_URL` come from the environment;
  never request keys in chat or embed them in files. Preserve an already authorized
  live replay choice. If no key is configured, explain what is missing without
  claiming a simulated result is live.
- Exit code 1 means a failed replay result was saved; inspect that result.
  Exit code 2 means invalid inputs or a setup problem.
- `--tool module:function` executes a local implementation and can have side
  effects. Use only for an explicitly selected local tool rerun, not as an
  automatic fallback after a frozen result mismatch.

For full reruns, use the bundled `agent_replay.replay_agent(trace, agent, ...)`
against the user's explicit `agent(run)` entrypoint. It restarts from the beginning;
it does not resume a process checkpoint. Decorated tools use frozen observations
or named JSON fixtures. Uninstrumented code still executes normally. Task success
requires the user's assertions, not just a completed execution status.

## Capture

Read [capture.md](references/capture.md) when adding instrumentation. Make a
focused change at the user's actual model/tool boundaries. Use their chosen
project environment. Do not replace their framework or automatically run a
production agent merely to verify decorators.

For a key-free demonstration, run `demo --directory <project>/.replay/traces`.
To inspect in the UI, run `serve --directory <project>/.replay/traces` and use the
loopback URL printed by the process only when that browser can reach it. The
hosted demo at https://agent-replay-debugger.vercel.app supports manual JSON import;
it does not receive local traces automatically.
