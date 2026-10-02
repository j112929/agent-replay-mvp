"""Build leakage-safe trajectory-risk datasets from replay/regression artifacts."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Any
from .migrations import to_v2
from .schema import validate_trace
from .trajectory_model import extract_features

DEFAULT_PREFIXES = (0.2, 0.4, 0.6, 0.8, 1.0)

def _bucket(trace_id: str) -> str:
    x = int(hashlib.sha256(trace_id.encode()).hexdigest()[:8], 16) % 100
    return "train" if x < 70 else "calibration" if x < 85 else "test"

def _prefix(trace: dict[str, Any], count: int) -> dict[str, Any]:
    value = dict(trace)
    value["steps"] = list(trace.get("steps") or [])[:count]
    value["execution"] = dict(trace.get("execution") or {})
    value["execution"]["status"] = "running" if count < len(trace.get("steps") or []) else value["execution"].get("status", "completed")
    # Never leak terminal failure/evaluation annotations into partial features.
    value["failures"] = [] if count < len(trace.get("steps") or []) else list(trace.get("failures") or [])
    value.pop("evaluation", None)
    return value

def samples_from_trace(trace: dict[str, Any], outcome: int, prefixes=DEFAULT_PREFIXES) -> list[dict[str, Any]]:
    trace = to_v2(validate_trace(trace))
    steps = trace["steps"]
    if not steps:
        return []
    counts = sorted({max(1, min(len(steps), round(len(steps) * p))) for p in prefixes})
    split = _bucket(trace["id"])
    return [{
        "trace_id": trace["id"], "prefix_steps": n, "total_steps": len(steps),
        "progress": n / len(steps), "split": split, "label": int(outcome),
        "features": extract_features(_prefix(trace, n)),
    } for n in counts]

def build_from_directory(root: str | Path, prefixes=DEFAULT_PREFIXES) -> list[dict[str, Any]]:
    root = Path(root)
    traces: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for p in root.rglob("*.json"):
        try:
            value = json.loads(p.read_text())
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if isinstance(value, dict) and value.get("schema_version") in ("1.0", "2.0") and isinstance(value.get("steps"), list):
            try: traces[value["id"]] = to_v2(validate_trace(value))
            except (ValueError, KeyError, TypeError): pass
        if isinstance(value, dict) and value.get("candidate_trace_id") and value.get("verdict") in ("pass", "fail"):
            results.append(value)
        if isinstance(value, dict) and isinstance(value.get("results"), list):
            results.extend(r for r in value["results"] if isinstance(r, dict) and r.get("candidate_trace_id") and r.get("verdict") in ("pass", "fail"))

    labels: dict[str, int] = {}
    for result in results:
        tid = result["candidate_trace_id"]; label = 1 if result["verdict"] == "pass" else 0
        if tid in labels and labels[tid] != label:
            continue
        labels[tid] = label

    samples = []
    for tid, label in labels.items():
        if tid in traces:
            samples.extend(samples_from_trace(traces[tid], label, prefixes))
    return samples

def write_jsonl(samples: list[dict[str, Any]], path: str | Path) -> None:
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in samples: f.write(json.dumps(row, sort_keys=True) + "\n")
