"""Single-step experiments and opt-in whole-agent reruns using frozen tools."""
import copy
import datetime as dt
import inspect
import time
import uuid
from .schema import VERSION, validate_trace, sanitize
from .provider import completion

class RecordedError(RuntimeError):
    """A recorded tool or model failure, never a new external execution."""

class ReplayMismatch(RuntimeError):
    """A changed call cannot be safely matched to a captured tool observation."""

def replay_step(trace, step_id, *, mode="recorded", fixture=None, model=None, base_url=None, api_key=None, tool_fn=None):
    validate_trace(trace)
    source = next((s for s in trace["steps"] if s["id"] == step_id), None)
    if source is None:
        raise ValueError("Step not found")
    if mode not in ("recorded", "fixture", "live", "tool"):
        raise ValueError("Unknown replay mode")
    if mode in ("fixture", "tool") and source["kind"] != "tool":
        raise ValueError("Tool replay requires a tool step")
    if mode == "live" and (source["kind"] != "llm" or not isinstance(source.get("request"), dict)):
        raise ValueError("Live replay requires an LLM step with a captured Chat Completions request")
    if mode == "tool" and not callable(tool_fn):
        raise ValueError("An explicit local tool function is required")
    event = copy.deepcopy(source)
    event.update(id=str(uuid.uuid4()), source_step_id=step_id, start_ms=0, replay_mode=mode)
    event.pop("parent_id", None)
    if mode != "recorded":
        event.update(output=None, error=None, status="running", duration_ms=0)
        event.pop("usage", None)
        event.pop("response", None)
    started = time.perf_counter()
    try:
        if mode == "fixture":
            event.update(output=sanitize(fixture), status="success")
        elif mode == "live":
            request = copy.deepcopy(source["request"])
            request["model"] = model or source.get("model") or request.get("model")
            event.update(model=request["model"], request=sanitize(request), input=sanitize(request))
            payload = completion(request, base_url=base_url, api_key=api_key)
            event.update(output=sanitize(payload["choices"][0]["message"]), response=sanitize(payload), usage=sanitize(payload.get("usage", {})), status="success")
        elif mode == "tool":
            if not isinstance(source.get("input"), dict):
                raise ValueError("Tool replay expects input to be keyword arguments")
            value = tool_fn(**source["input"])
            if inspect.isawaitable(value):
                import asyncio
                value = asyncio.run(value)
            event.update(output=sanitize(value), status="success")
    except Exception as exc:
        event.update(status="error", error={"type": type(exc).__name__, "message": sanitize(str(exc))})
    if mode not in ("recorded", "fixture"):
        event["duration_ms"] = round((time.perf_counter()-started)*1000, 3)
    return {"schema_version": VERSION, "id": str(uuid.uuid4()), "name": trace["name"], "started_at": dt.datetime.now(dt.timezone.utc).isoformat(), "status": event["status"], "duration_ms": event["duration_ms"], "steps": [event], "tags": ["replay"], "replay": {"source_trace_id": trace["id"], "source_step_id": step_id, "mode": mode, "scope": "step", "application_validated": False}, "metadata": {"note": "Single-step experiment. Downstream agent steps were not executed. Recorded mode preserves historical timing."}}

class ReplayPolicy:
    def __init__(self, trace, model=None, overrides=None):
        self.events = copy.deepcopy(trace["steps"])
        self.used = set()
        self.model = model
        self.overrides = overrides or {}

    def _match(self, name, kind, inputs):
        for event in self.events:
            if event["id"] not in self.used and event["name"] == name and event["kind"] == kind and event.get("input") == sanitize(inputs):
                self.used.add(event["id"])
                if event["status"] == "error":
                    error = event.get("error") or {}
                    raise RecordedError(f"{error.get('type', 'Error')}: {error.get('message', 'Recorded failure')}")
                return event
        raise ReplayMismatch(f"No unused recorded result for {kind} {name} with these arguments. Supply a tool override for a changed branch.")

    def resolve(self, name, kind, inputs):
        if name in self.overrides:
            # Values are explicit JSON fixtures, not imported executable code.
            return copy.deepcopy(self.overrides[name])
        return copy.deepcopy(self._match(name, kind, inputs).get("output"))

    def resolve_llm(self, request):
        event = self._match("chat.completions", "llm", request)
        if isinstance(event.get("response"), dict):
            return copy.deepcopy(event["response"])
        return {"choices": [{"message": copy.deepcopy(event.get("output")), "index": 0, "finish_reason": "stop"}], "usage": copy.deepcopy(event.get("usage", {}))}

def replay_agent(trace, agent, *, model=None, tool_overrides=None, directory=".replay/traces"):
    """Rerun an explicit agent(run) entrypoint from the start. No checkpoint resume.

    Decorated tools use matched recordings or named fixtures. run.chat uses live
    completions when model is provided. Uninstrumented application code executes
    normally, so this is not a sandbox. A completed rerun is not task correctness.
    """
    from .capture import Capture
    validate_trace(trace)
    policy = ReplayPolicy(trace, model, tool_overrides)
    run = Capture(trace["name"], directory, _policy=policy)
    run.trace["replay"] = {"source_trace_id": trace["id"], "mode": "agent_rerun", "scope": "agent", "application_validated": False, "model": model}
    run.trace["metadata"] = {"note": "Agent entrypoint rerun from start. Instrumented tools use frozen observations or fixtures; uninstrumented code executes normally."}
    run.trace["tags"] = ["replay"]
    try:
        with run:
            result = agent(run)
            if inspect.isawaitable(result):
                import asyncio
                result = asyncio.run(result)
            run.trace["output"] = sanitize(result)
    except Exception:
        # The exception was captured and persisted; callers inspect trace.status.
        if run.path is None:
            raise
    return run


async def async_replay_agent(trace, agent, *, model=None, tool_overrides=None, directory=".replay/traces"):
    """Awaitable counterpart to the compatible replay_agent API."""
    from .capture import Capture
    from .migrations import legacy
    validate_trace(trace)
    trace = legacy(trace)
    policy = ReplayPolicy(trace, model, tool_overrides)
    run = Capture(trace['name'], directory, _policy=policy)
    run.trace['replay'] = {'source_trace_id':trace['id'],'mode':'agent_rerun','scope':'agent','application_validated':False}
    try:
        with run:
            result = agent(run)
            if inspect.isawaitable(result): result = await result
            run.set_output(result)
    except Exception:
        if run.path is None: raise
    return run
