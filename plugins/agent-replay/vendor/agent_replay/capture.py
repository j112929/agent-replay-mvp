"""Context-aware sync/async capture with stable call ordering and parent IDs."""
import contextvars
import datetime as dt
import functools
import inspect
import time
import uuid
import copy
import random
import warnings
import threading
from pathlib import Path
from .migrations import to_v2
from .serialization import MISSING
from .provenance import collect
from .journal import Journal
from .storage import save
from .schema import VERSION, sanitize, write_trace
from .provider import completion

_active = contextvars.ContextVar("agent_replay_capture", default=None)
_parent = contextvars.ContextVar("agent_replay_parent", default=None)
_event = contextvars.ContextVar("agent_replay_event", default=None)

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
        maximum = min(2000, self.run.policy.spec.get("limits",{}).get("max_steps",2000)) if self.run.policy and hasattr(self.run.policy,"spec") else 2000
        if len(self.run.trace["steps"]) >= maximum:
            raise ValueError("A trajectory supports at most 2,000 steps")
        self.started = time.perf_counter()
        self.event.update(start_ms=round((self.started-self.run._start)*1000, 3), duration_ms=0, parent_id=_parent.get())
        parent = next((s for s in self.run.trace['steps'] if s['id'] == self.event['parent_id']), None)
        self.event['scope_path'] = (parent.get('scope_path', []) + [parent.get('boundary_id', parent['name'])]) if parent else []
        key = (self.event.get('boundary_id', self.event['name']), tuple(self.event['scope_path']), self.event.get('lane_id'))
        with self.run._span_lock:
            self.run._counts[key] = self.run._counts.get(key, 0) + 1
            self.event['occurrence'] = self.run._counts[key]
            self.run.trace["steps"].append(self.event)
        self.event_token = _event.set(self.event)
        self.run._record('step_started', self.event)
        self.token = _parent.set(self.event["id"])
        return self

    def set_output(self, output):
        self.event["output"] = self.run._clean(output)
        return output

    def __exit__(self, exc_type, exc, tb):
        self.event["duration_ms"] = round((time.perf_counter()-self.started)*1000, 3)
        self.event["status"] = "error" if exc is not None else "success"
        if exc is not None:
            self.event["error"] = {"type": type(exc).__name__, "message": sanitize(str(exc))}
        try:
            self.run._record('step_failed' if exc is not None else 'step_completed', self.event)
        except Exception:
            if exc is None: raise
            warnings.warn('Capture write failed while preserving the application exception', RuntimeWarning)
        _event.reset(self.event_token)
        _parent.reset(self.token)
        return False

class Capture:
    def __init__(self, name, directory=".replay/traces", metadata=None, *, _policy=None, input=MISSING, entrypoint_id=None, durability=None, on_capture_error="warn", redactor=None):
        if not isinstance(name, str) or not name.strip() or len(name) > 240:
            raise ValueError("Capture name must be a non-empty string (max 240 characters)")
        if on_capture_error not in ('warn','raise'): raise ValueError('Invalid capture error policy')
        self.directory = directory
        self.input = None if input is MISSING else input
        self.modern = durability is not None or input is not MISSING or entrypoint_id is not None
        self.durability = durability or 'buffered'
        self.on_capture_error = on_capture_error
        self.redactor = redactor
        self.journal = None
        self._counts = {}
        self._span_lock = threading.Lock()
        self.random = random.Random()
        self.clock = time.time
        self.path = None
        self.policy = _policy
        self._entered = self._closed = False
        self.trace = {"schema_version": VERSION, "id": str(uuid.uuid4()), "name": name, "started_at": dt.datetime.now(dt.timezone.utc).isoformat(), "status": "running", "duration_ms": 0, "steps": [], "metadata": sanitize(metadata or {}), "tags": []}

    @property
    def _current_event(self):
        return _event.get()

    def _clean(self, value):
        cleaned = sanitize(value)
        if not self.redactor: return cleaned
        try:
            return sanitize(self.redactor(cleaned))
        except Exception:
            self.trace.setdefault('capture_health', {})['state'] = 'partial'
            if self.on_capture_error == 'raise': raise
            warnings.warn('Capture redactor failed; value omitted', RuntimeWarning)
            return '[CAPTURE_ERROR]'

    def _record(self, kind, value):
        if not self.journal: return
        try:
            self.journal.append(kind, self._clean(copy.deepcopy(value)))
        except Exception as exc:
            self.trace['capture_health']['state'] = 'partial'
            self.trace['capture_health']['persistence_errors'] += 1
            if self.on_capture_error == 'raise': raise
            warnings.warn('Agent Replay capture persistence failed: ' + type(exc).__name__, RuntimeWarning)

    def set_output(self, value):
        self.trace['output'] = self._clean(value)
        self._record('run_output', self.trace['output'])
        return value

    def fail(self, message, category='manual', step_id=None):
        failure = {'id': str(uuid.uuid4()), 'category': category, 'message': self._clean(message), 'step_id': step_id}
        self.trace.setdefault('failures', []).append(failure)
        self._record('failure_observed', failure)

    def __enter__(self):
        if self._entered:
            raise RuntimeError("Capture objects are single-use")
        self._entered = True
        self._start = time.perf_counter()
        self.trace["started_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        if self.modern:
            self.trace['input'] = self._clean(self.input)
            self.trace['provenance'] = collect()
            self.trace['capture_health'] = {'state':'complete','dropped_events':0,'persistence_errors':0}
            try:
                self.journal = Journal(Path(self.directory).parent / 'journals' / (self.trace['id']+'.jsonl'), self.durability)
                self._record('run_started', self.trace)
            except Exception:
                if self.on_capture_error == 'raise': raise
                self.trace['capture_health']['state'] = 'partial'
                warnings.warn('Agent Replay journal unavailable', RuntimeWarning)
        self._token = _active.set(self)
        self._parent_token = _parent.set(None)
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None: self.on_capture_error = "warn"
        self.trace["duration_ms"] = round((time.perf_counter()-self._start)*1000, 3)
        self.trace["status"] = "error" if exc is not None else "success"
        if exc is not None:
            self.trace["error"] = {"type": type(exc).__name__, "message": sanitize(str(exc))}
        self._closed = True
        _active.reset(self._token)
        _parent.reset(self._parent_token)
        if self.modern:
            if exc is not None: self.fail(str(exc), category='exception')
            self._record('run_finished', self.trace)
            try:
                self.path = save(Path(self.directory) / (self.trace['id']+'.json'), to_v2(self._clean(self.trace)))
            except Exception:
                if self.on_capture_error == 'raise' and exc is None: raise
                warnings.warn('Agent Replay final trace could not be saved', RuntimeWarning)
            finally:
                if self.journal: self.journal.close()
        else:
            self.path = write_trace(self.trace, self.directory)
        return False

    def span(self, name, *, kind="agent", input=None, **extra):
        return Span(self, name, kind, input, **extra)

    def model(self, request, *, provider_id, project):
        """Provider-neutral text/tool blocks. Project is explicit trusted configuration."""
        from .providers.normalize import to_chat, from_chat
        from .providers.registry import complete
        body = to_chat(request)
        with self.span('model', kind='llm', boundary_id=request.get('boundary_id','model'), input=body, request=body, model=body['model']) as event:
            if self.policy:
                payload = self.policy.resolve_llm(body)
            else:
                payload = complete(project, provider_id, body)
                event.event['provider_kind'] = project['providers'][provider_id].get('kind','openai_compatible')
            event.event['response'] = self._clean(payload)
            event.event['usage'] = self._clean(payload.get('usage',{}))
            event.set_output(payload['choices'][0]['message'])
            return from_chat(payload)

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

def step(_fn=None, *, name=None, kind="agent", model=None, boundary_id=None, lane_id=None, side_effect="unknown"):
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
            with run.span(label, kind=kind, input=inputs, boundary_id=boundary_id or label, lane_id=lane_id, tool={"side_effect":side_effect}, **({"model": model} if model else {})) as event:
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
            with run.span(label, kind=kind, input=inputs, boundary_id=boundary_id or label, lane_id=lane_id, tool={"side_effect":side_effect}, **({"model": model} if model else {})) as event:
                if run.policy and kind in ("tool", "llm"):
                    event.event["replay_mode"] = "fixture" if label in run.policy.overrides else "recorded"
                    output = run.policy.resolve(label, kind, inputs)
                else:
                    output = await fn(*args, **kwargs)
                if inspect.isawaitable(output): output = await output
                return event.set_output(output)
        return async_wrapped if inspect.iscoroutinefunction(fn) else wrapped
    return decorate(_fn) if _fn is not None else decorate

def tool(_fn=None, *, name=None, boundary_id=None, lane_id=None, side_effect="unknown"):
    return step(_fn, name=name, kind="tool", boundary_id=boundary_id, lane_id=lane_id, side_effect=side_effect)
