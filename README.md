# Replay — Agent Replay Debugger

[Live demo](https://agent-replay-debugger.vercel.app) · [Source](https://github.com/j112929/agent-replay-mvp)

A local-first, MIT-licensed MVP to capture agent trajectories, inspect failures,
and compare replay experiments. Python 3.10+, no runtime dependencies.

**v0.1 is a working debugger and SDK.** Instrument the calls you care about;
import the resulting JSON into the web UI. Replay a stored observation, replace
a tool response, call another model, or rerun an explicit agent entrypoint.

## Five-minute quickstart

Download and unzip the source, then run inside `agent-replay-mvp/`:

```bash
python -m pip install -e .
python examples/capture_demo.py
agent-replay serve
```

Open **http://127.0.0.1:8765**. The runner loads `.replay/traces/`; use **Sync**
after capturing more runs. Select the failed tool, click **Replay step**, apply
a JSON fixture, and inspect the structural output diff. **Export JSON** saves a
portable trajectory. The six built-in UI examples are synthetic and labelled
DEMO. The local demo performs no provider or payment requests.

The online version supports JSON import/export, inspection, recorded replay,
and tool fixtures. Live API calls require the local runner. Download the full
source from **Quickstart → Download MVP** in the interface.

The package is supplied as source; it has not been published to PyPI. Installation
uses setuptools as a build dependency. An environment with setuptools already
available can install offline using `python -m pip install -e . --no-build-isolation`.

## Capture any explicitly instrumented Python agent

```python
from agent_replay import capture, tool

@tool
def lookup_order(order_id: str):
    return {"id": order_id, "status": "delivered"}

with capture("support-agent") as run:
    order = lookup_order(order_id="1042")

print(run.path)
```

`@tool` supports synchronous and asynchronous functions and records arguments,
outputs, timing, parent IDs, and exceptions. `@step(kind="agent")` and
`@step(kind="llm", model="your-model")` capture other functions. Outside a
capture block, decorators leave normal function execution unchanged. A capture
block saves JSON on normal exit and exceptions; it is not crash-durable during
process termination. Await all child tasks before leaving the block.

For any framework without an adapter, capture an explicit span:

```python
with capture("custom-agent") as run:
    with run.span("planner", kind="agent", input={"task": "inspect failure"}) as span:
        result = your_agent.invoke({"task": "inspect failure"})
        span.set_output(result)
```

Capturing only an outer span does not auto-discover nested framework events.
Instrument individual model and tool boundaries for useful replay. Arbitrary
Python objects are represented by their type unless they have a dataclass or
Pydantic JSON representation; this is not an object-state snapshot.

## Capture and replay models

The included transport supports non-streaming **OpenAI-compatible Chat
Completions**. API contract:
[official Chat Completions reference](https://developers.openai.com/api/reference/resources/chat).

Set `OPENAI_API_KEY` in the terminal where you start the runner. Configure
`OPENAI_BASE_URL` for another compatible provider or a local model server.
Use the model IDs supported by that endpoint. The UI accepts a target model ID;
the endpoint and credentials remain in the runner environment.

```python
from agent_replay import capture

with capture("research-agent") as run:
    response = run.chat(
        model="your-provider-model-id",
        messages=[{"role": "user", "content": "Explain HTTP 429 recovery."}],
        max_completion_tokens=256,
    )
    print(response["choices"][0]["message"])
```

`run.chat` records the request, full provider response, assistant message,
usage when supplied, and timing. Model replay sends the captured messages and
parameters with the replacement model ID. Provider-specific unsupported
parameters are reported as actual errors. The SDK does not silently translate
between native Anthropic, Responses, or other provider protocols. HTTP is
accepted only for loopback model servers; other endpoints require HTTPS.
Streaming is explicitly rejected before sending a request.

```bash
agent-replay replay path/to/trace.json --step EXACT_STEP_ID --model target-model
agent-replay replay path/to/trace.json --step EXACT_STEP_ID --model other-model --base-url https://your-provider.example/v1
```

The command saves a new trace and prints its path. Model-generated tool calls
are inspected as output; single-step replay does not execute those tools or
continue an agent loop. Calls incur your model provider's normal charges.

## Replay modes and guarantees

| Mode | What executes | What the result establishes |
| --- | --- | --- |
| Recorded | Nothing; stored output/error is copied | The recorded observation is reproduced; timing is historical |
| Tool fixture | Nothing external; supplied JSON replaces a tool output | The substitution is applied; downstream recovery is untested |
| Live model | One real Chat Completions request through the local runner | Actual model output/error and measured latency |
| Local tool | Your explicit `module:function` is called with recorded keyword arguments | Actual replacement implementation output/error |
| Agent rerun | Your explicit `agent(run)` starts again, with frozen decorated tools or fixtures | The new execution path; task correctness requires your assertions |

Original traces are not mutated. Single-step experiments intentionally include
only the selected step. A successful fixture does not mean the agent recovered.
Every replay declares `application_validated: false` until your evaluation code
establishes task correctness.

Tool CLI examples:

```bash
agent-replay replay path/to/trace.json --step EXACT_STEP_ID
agent-replay replay path/to/trace.json --step EXACT_STEP_ID --fixture examples/fixture.json
agent-replay replay path/to/trace.json --step EXACT_STEP_ID --tool my_tools:replacement_lookup
```

`--tool` explicitly executes your local function and can have its normal side
effects. Use an importable module and a callable accepting the recorded keyword
arguments. Sync and async callables are supported. It is not offered through the
web API, and trace imports never execute Python code.

## Full agent reruns

```python
from agent_replay import replay_agent

experiment = replay_agent(
    original_trace,
    agent,  # your synchronous or asynchronous callable: agent(run)
    tool_overrides={"payments.get_transaction": {"refundable": True}},
    # model="other-model",  # run.chat becomes a live call if provided
)
print(experiment.trace["status"], experiment.path)
```

Run `python examples/rerun_agent.py` for a complete failure → tool substitution
→ successful local branch example. `replay_agent` runs from the start, not from
a process checkpoint. Decorated tools match by name, kind, and sanitized
arguments, consuming each recording once. A changed branch without a fixture
produces `ReplayMismatch` instead of silently calling a real tool. Named tool
fixtures are reused for every call of that name; parameter-sensitive fixtures
are not implemented. Agent code and uninstrumented functions still execute
normally; this runner is not a sandbox. Recorded exceptions are raised as
`RecordedError`, so exception-type-specific branches may need an adapter.

`run.chat` is the automatic model-substitution boundary. Generic `@step(kind="llm")`
functions use frozen results; model substitution requires using `run.chat`.
Async entrypoints are accepted from a synchronous caller. Call `replay_agent`
outside an already running event loop (or use `asyncio.to_thread`).

## Storage and data

Traces live in `.replay/traces/` and browser imports/replays in browser local
storage. The hosted page does not upload imported traces to an application
server. Browser storage is device-specific and capacity-limited; export any run
you need to retain independently. The local HTTP runner listens only on loopback,
rejects cross-origin requests, limits replay bodies to 5 MB, and does not expose
trace-directory browsing or an arbitrary function execution endpoint.

Common credential fields and bearer/API-key patterns are redacted recursively.
This is best-effort credential filtering, **not** complete PII or secret detection.
Free-text prompts, tool payloads, and error messages can contain sensitive data;
provide your own redaction at the capture boundary where needed. Redacted values
cannot be faithfully reconstructed for replay. SDK inputs and outputs are
snapshots; external state, code versions, random seeds, files, and databases are
not automatically captured. Remote APIs and nondeterministic models cannot
promise bit-for-bit reproduction.

## JSON format

`schema/trajectory.schema.json` defines the portable v1.0 envelope. The UI imports
one trajectory or an array of up to 100, at most 5 MB per file and 2,000 steps per
trajectory. SDK and UI validate IDs, status, timing, kinds, and version before
accepting traces.

| Field | Purpose |
| --- | --- |
| `schema_version`, `id`, `name`, `started_at` | Version and run identity |
| `status`, `duration_ms` | Captured execution state and wall time |
| `steps[]` | Agent, LLM, and tool events in start order |
| `steps[].input/output/error` | Serialized execution boundary data |
| `steps[].request/response/model/usage` | Captured model call details when available |
| `steps[].parent_id/start_ms/duration_ms` | Nesting and timing |
| `replay` | Source run/step, replay mode and scope |

## Development and verification

The frontend is dependency-free HTML/CSS/ES modules. Serve it with the Python
runner. It does not require Node, a bundler, or a remote database to run.

```bash
python -m unittest discover -s tests -v
node --test tests/core.test.mjs
python scripts/package_release.py
```

The release script bundles UI assets for Python wheels and produces the source
download at `dist/agent-replay-mvp.zip`. Tests cover exception persistence,
credential redaction, async parentage, replay isolation, changed-argument
matching, provider transport against a local mock, and same-origin enforcement.
Actual paid-provider calls and browser visual QA were not performed for this
MVP. Optional WebMCP registration is feature-detected; validation in a supported
browser context was unavailable.

## Deliberate v0.1 limits

- Explicit Python instrumentation; no automatic LangGraph, CrewAI, MCP, or JS adapters.
- No streaming, binary/multimodal snapshots, native provider-specific transports,
  remote execution sandbox, or checkpoint resume.
- Model comparisons are individual replay experiments rather than a batch benchmark.
- No authentication for the loopback runner, team backend, hosted trace ingestion,
  background retention, or billing. The online workspace is a convenient local-data UI.
- Full reruns require your original agent code, environment, and explicit entrypoint.

MIT license. Source repository: https://github.com/j112929/agent-replay-mvp.
The Python package has not been published to PyPI.

## Vercel deployment

Import this repository in Vercel. The included `vercel.json` serves `dist/` as a static site without a build or dependency installation. Model calls still use the local Python runner. The existing production deployment was uploaded directly; automatic Git deployments require connecting this repository in Vercel.
