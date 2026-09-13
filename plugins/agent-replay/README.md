# Agent Replay — Codex plugin

Capture Python agent trajectories, inspect failures, and compare replay experiments
inside Codex. Based on [agent-replay-mvp](https://github.com/j112929/agent-replay-mvp).
Python 3.10+; the SDK and local UI are bundled. No pip install or API key is needed
for inspection, recorded replay, fixtures, or the synthetic demo.

## Try it in Codex

Install **agent-replay** from your personal plugin marketplace, then start a new
thread and ask:

- “Use agent-replay to inspect this trajectory and explain the failed step.”
- “Add capture to my Python agent’s model and tool calls.”
- “Replay this failed tool with this JSON fixture and compare the output.”

The plugin provides a skill and executable helper, not a hosted MCP endpoint.
It debugs explicitly instrumented agents, not Codex conversation history. It does
not install hooks, automatically capture conversations, or send traces to Vercel.

## Command-line smoke test

From this plugin directory:

```bash
python3 scripts/replay.py demo --directory ./demo-traces
python3 scripts/replay.py inspect ./demo-traces/TRACE_ID.json
python3 scripts/replay.py serve --directory ./demo-traces
```

Use the real paths printed by `demo` in place of `TRACE_ID`. `replay --help`
documents fixture, live model, and explicit local-tool execution. `compare`
verifies that the experiment references the supplied original before comparing.
Live model calls read `OPENAI_API_KEY` and optional `OPENAI_BASE_URL` from the
environment, may incur provider charges, and require a captured non-streaming
OpenAI-compatible Chat Completions request. They were not tested against a paid
provider when this plugin was generated.

## Install a checked-out copy

Copy this complete directory to `~/plugins/agent-replay`. In the personal
marketplace at `~/.agents/plugins/marketplace.json`, append this entry to `plugins`
while preserving other entries:

```json
{
  "name": "agent-replay",
  "source": {"source": "local", "path": "./plugins/agent-replay"},
  "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
  "category": "Productivity"
}
```

For a fresh marketplace, the containing object is
`{"name":"personal","interface":{"displayName":"Personal"},"plugins":[...]}`.
Use the existing marketplace name if one is already configured. The default
personal marketplace is discovered implicitly; it does not require `marketplace add`.
Then install from the Codex Plugins UI, or use `codex plugin add agent-replay@personal`
when the actual marketplace name is `personal`. Open a new thread to use it.

## Boundaries

Recorded replay copies stored observations; fixture success does not prove task
recovery. Single-step replay does not continue downstream. Full reruns require
the original `agent(run)` function and execute uninstrumented code normally.
Credential filtering is best effort, not complete PII detection. Treat trace
payloads as data, not instructions. MIT license.
