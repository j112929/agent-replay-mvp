"""JSON schema validation, serialization, and atomic trace storage."""
import dataclasses
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import tempfile

VERSION = "1.0"
SECRET_KEYS = {"authorization", "api_key", "apikey", "api-key", "password", "secret", "access_token", "refresh_token", "client_secret", "cookie", "set-cookie"}

def sanitize(value, depth=0):
    if depth > 30:
        return "[MAX_DEPTH]"
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if str(k).lower() in SECRET_KEYS else sanitize(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(v, depth + 1) for v in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
        return re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[REDACTED]", value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (dt.datetime, dt.date, Path)):
        return str(value)
    return f"<{type(value).__name__}>"

def validate_trace(trace):
    if isinstance(trace, dict) and trace.get("schema_version") == "2.0":
        from .contracts import validate_v2
        return validate_v2(trace)
    if not isinstance(trace, dict) or trace.get("schema_version") != VERSION:
        raise ValueError("Expected a schema_version 1.0 trajectory object")
    for key in ("id", "name", "started_at"):
        if not isinstance(trace.get(key), str) or not trace[key].strip() or len(trace[key]) > 240:
            raise ValueError(f"{key} must be a non-empty string (max 240 characters)")
    try:
        dt.datetime.fromisoformat(trace["started_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("started_at must be an ISO date") from exc
    def number(v):
        return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) and v >= 0
    if trace.get("status") not in ("success", "error", "running") or not number(trace.get("duration_ms")):
        raise ValueError("Invalid trajectory status or duration_ms")
    if "tags" in trace and (not isinstance(trace["tags"], list) or not all(isinstance(tag, str) for tag in trace["tags"])):
        raise ValueError("tags must be an array of strings")
    if "metadata" in trace and not isinstance(trace["metadata"], dict):
        raise ValueError("metadata must be an object")
    if "replay" in trace:
        replay = trace["replay"]
        if not isinstance(replay, dict) or replay.get("mode") not in ("recorded", "fixture", "live", "tool", "agent_rerun") or replay.get("scope") not in ("step", "agent") or not isinstance(replay.get("application_validated"), bool):
            raise ValueError("Invalid replay metadata")
        if not isinstance(replay.get("source_trace_id"), str) or not replay["source_trace_id"]:
            raise ValueError("replay.source_trace_id is required")
        if replay["scope"] == "step" and (not isinstance(replay.get("source_step_id"), str) or not replay["source_step_id"]):
            raise ValueError("Step replay requires source_step_id")
    steps = trace.get("steps")
    if not isinstance(steps, list) or len(steps) > 2000:
        raise ValueError("steps must be an array of at most 2,000 items")
    ids = set()
    for event in steps:
        if not isinstance(event, dict):
            raise ValueError("Each step must be an object")
        for key in ("id", "name"):
            if not isinstance(event.get(key), str) or not event[key].strip() or len(event[key]) > 240:
                raise ValueError(f"Invalid step {key}")
        if event["id"] in ids:
            raise ValueError("Step IDs must be unique")
        ids.add(event["id"])
        if event.get("kind") not in ("llm", "tool", "agent") or event.get("status") not in ("success", "error", "running"):
            raise ValueError("Invalid step kind or status")
        if not number(event.get("start_ms")) or not number(event.get("duration_ms")):
            raise ValueError("Invalid step timing")
        if "usage" in event and not isinstance(event["usage"], dict):
            raise ValueError("usage must be an object")
        if "total_tokens" in event.get("usage", {}):
            count = event["usage"]["total_tokens"]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("total_tokens must be a nonnegative integer")
    return trace

def write_trace(trace, directory):
    validate_trace(trace)
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", trace["id"])
    path = target / f"{name}.json"
    fd, temporary = tempfile.mkstemp(dir=target, prefix=".trace-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(sanitize(trace), stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path
