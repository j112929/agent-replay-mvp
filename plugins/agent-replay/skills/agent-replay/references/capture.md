# Instrumentation boundaries

The plugin bundles the project's Python SDK under `<plugin>/vendor`. For a
one-off execution, prepend that directory to Python's import path in your
launcher. For a durable application dependency, install the user's source
checkout with `python3 -m pip install -e <agent-replay-mvp-checkout>` using the
project's environment. The package is not published to PyPI.

```python
from agent_replay import capture, tool

@tool
def lookup(order_id: str):
    return existing_lookup(order_id)

with capture("support-agent", directory=".replay/traces") as run:
    order = lookup(order_id="1042")
```

`@tool` supports sync and async functions. Await all tasks before the capture
block exits. Exceptions propagate and are persisted on block exit. Killing the
process mid-run is not crash-durable capture. An outer span alone does not discover
nested framework calls; instrument individual boundaries for useful replay.

Use `run.chat(model=..., messages=..., **parameters)` for non-streaming,
OpenAI-compatible Chat Completions. It captures the request and full response,
and enables replacement-model replay. Generic `@step(kind="llm")` captures a
function boundary but does not construct a provider request or enable automatic
model substitution. Native Responses/Anthropic protocols and streaming need an
adapter; do not label them as supported by this version.

The capture context also exposes `run.span(name, kind="agent", input=value)`;
call `span.set_output(result)` inside it. Serialized arbitrary objects may be
type placeholders; dataclasses/Pydantic values can be serialized. This is not a
snapshot of process memory, files, databases, or nondeterministic external state.

For a full rerun:

```python
from agent_replay import replay_agent

experiment = replay_agent(
    original_trace,
    agent,  # callable accepting the capture run
    tool_overrides={"lookup": {"status": "delivered"}},
    directory=".replay/traces",
)
```

With `model=...`, `run.chat` issues live calls; decorated tools remain frozen or
overridden. Without it, captured model results are matched too. Changed arguments
without a fixture produce `ReplayMismatch`. Fixtures apply to every call of that
name. Recorded exceptions become `RecordedError`, which can change code that
catches a particular original exception type. The helper runs async entrypoints
from a synchronous caller; do not call it inside a running event loop.
