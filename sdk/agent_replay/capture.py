"""Context-aware sync/async capture with stable call ordering and parent IDs."""
import contextvars
import datetime as dt
import functools
import inspect
import time
import uuid
from .schema import VERSION, sanitize, write_trace
from .provider import completion

_active = contextvars.ContextVar("agent_replay_capture", default=None)
_parent = contextvars.ContextVar("agent_replay_parent", default=None)

class Span:
    def __init__(self, run, name, kind="agent", input=None, **extra):
        if kind not in ("llm", "tool", "agent"):
            raise ValueError("kind must be llm, tool, or agent")
        if not isinstance(name, str) or not name.strip() or len(name) > 240:
            raise ValueError("Step name must be a non-empty string (max 240 characters)")
        self.run = run
        self.event = {"id": str(uuid.uuid4()), "name": name, "kind": kind, "status": "running", "input": sanitize(input), "output": None, "error": None, **sanitize(extra)}

    def __enter__(self):
        if not self.run._entered or self.run._closed:
            raise RuntimeError("Spans must run inside an active capture block")
        if len(self.run.trace["steps"]) >= 2000:
            raise ValueError("A trajectory supports at most 2,000 steps")
        self.started = time.perf_counter()
        self.event.update(start_ms=round((self.started-self.run._start)*1000, 3), duration_ms=0, parent_id=_parent.get())
        self.run.trace["steps"].append(self.event)
        self.token = _parent.set(self.event["id"])
        return self

    def set_output(self, output):
        self.event["output"] = sanitize(output)
        return output

    def __exit__(self, exc_type, exc, tb):
        self.event["duration_ms"] = round((time.perf_counter()-self.started)*1000, 3)
        self.event["status"] = "error" if exc is not None else "success"
        if exc is not None:
            self.event["error"] = {"type": type(exc).__name__, "message": sanitize(str(exc))}
        _parent.reset(self.token)
        return False

class Capture:
    def __init__(self, name, directory=".replay/traces", metadata=None, *, _policy=None):
        if not isinstance(name, str) or not name.strip() or len(name) > 240:
            raise ValueError("Capture name must be a non-empty string (max 240 characters)")
        self.directory = directory
        self.path = None
        self.policy = _policy
        self._entered = self._closed = False
        self.trace = {"schema_version": VERSION, "id": str(uuid.uuid4()), "name": name, "started_at": dt.datetime.now(dt.timezone.utc).isoformat(), "status": "running", "duration_ms": 0, "steps": [], "metadata": sanitize(metadata or {}), "tags": []}

    def __enter__(self):
        if self._entered:
            raise RuntimeError("Capture objects are single-use")
        self._entered = True
        self._start = time.perf_counter()
        self.trace["started_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        self._token = _active.set(self)
        self._parent_token = _parent.set(None)
        return self

    def __exit__(self, exc_type, exc, tb):
        self.trace["duration_ms"] = round((time.perf_counter()-self._start)*1000, 3)
        self.trace["status"] = "error" if exc is not None else "success"
        if exc is not None:
            self.trace["error"] = {"type": type(exc).__name__, "message": sanitize(str(exc))}
        self._closed = True
        _active.reset(self._token)
        _parent.reset(self._parent_token)
        self.path = write_trace(self.trace, self.directory)
        return False

    def span(self, name, *, kind="agent", input=None, **extra):
        return Span(self, name, kind, input, **extra)

    def chat(self, *, model, messages, base_url=None, api_key=None, **params):
        if params.get("stream"):
            raise ValueError("Streaming is outside v0.1; use a non-streaming call")
        request = {"model": model, "messages": messages, **params}
        actual_model = self.policy.model if self.policy and self.policy.model else model
        request["model"] = actual_model
        with self.span("chat.completions", kind="llm", input=request, request=request, model=actual_model) as event:
            if self.policy and not self.policy.model:
                # Full frozen reruns use the recorded result; custom agent code still runs.
                event.event["replay_mode"] = "recorded"
                payload = self.policy.resolve_llm(request)
            else:
                payload = completion(request, base_url=base_url, api_key=api_key)
                if self.policy:
                    event.event["replay_mode"] = "live"
            message = payload["choices"][0]["message"]
            event.event["usage"] = sanitize(payload.get("usage", {}))
            event.event["response"] = sanitize(payload)
            event.set_output(message)
            # Return the full provider response, matching Chat Completions semantics.
            return payload

capture = Capture

def step(_fn=None, *, name=None, kind="agent", model=None):
    def decorate(fn):
        label = name or fn.__name__
        signature = inspect.signature(fn)
        def arguments(args, kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            return dict(bound.arguments)

        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            run = _active.get()
            if run is None:
                return fn(*args, **kwargs)
            inputs = arguments(args, kwargs)
            with run.span(label, kind=kind, input=inputs, **({"model": model} if model else {})) as event:
                if run.policy and kind in ("tool", "llm"):
                    event.event["replay_mode"] = "fixture" if label in run.policy.overrides else "recorded"
                    output = run.policy.resolve(label, kind, inputs)
                else:
                    output = fn(*args, **kwargs)
                return event.set_output(output)

        @functools.wraps(fn)
        async def async_wrapped(*args, **kwargs):
            run = _active.get()
            if run is None:
                return await fn(*args, **kwargs)
            inputs = arguments(args, kwargs)
            with run.span(label, kind=kind, input=inputs, **({"model": model} if model else {})) as event:
                if run.policy and kind in ("tool", "llm"):
                    event.event["replay_mode"] = "fixture" if label in run.policy.overrides else "recorded"
                    output = run.policy.resolve(label, kind, inputs)
                else:
                    output = await fn(*args, **kwargs)
                return event.set_output(output)
        return async_wrapped if inspect.iscoroutinefunction(fn) else wrapped
    return decorate(_fn) if _fn is not None else decorate

def tool(_fn=None, *, name=None):
    return step(_fn, name=name, kind="tool")
