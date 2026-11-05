"""Lightweight trajectory risk model for online agent execution decisions.

The MVP is dependency-free: it converts a partial v2 trajectory into stable numeric
features, applies a calibrated logistic baseline, and maps risk signals to an
intervention. The weights are intentionally explicit so they can later be replaced
by weights learned from replay/evaluation data without changing the API.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import math
from typing import Any

FAILURE_TYPES = ("none", "tool_execution", "model_execution", "looping", "high_cost", "unknown")
FEATURE_NAMES = ("step_count", "tool_fraction", "error_rate", "tool_error_rate", "llm_error_rate", "repeat_rate", "duration_s", "failure_markers")

@dataclass(frozen=True)
class RiskPrediction:
    success_probability: float
    failure_type: str
    confidence: float
    recommended_action: str
    features: dict[str, float]
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

def _status(step: dict[str, Any]) -> str:
    return str(step.get("execution", {}).get("status", ""))

def extract_features(trace: dict[str, Any]) -> dict[str, float]:
    steps = list(trace.get("steps") or [])
    n = max(len(steps), 1)
    tool = [s for s in steps if s.get("kind") == "tool"]
    llm = [s for s in steps if s.get("kind") == "llm"]
    errors = [s for s in steps if _status(s) in ("error", "interrupted")]
    tool_errors = [s for s in tool if _status(s) in ("error", "interrupted")]
    llm_errors = [s for s in llm if _status(s) in ("error", "interrupted")]
    durations = [float(s.get("execution", {}).get("duration_ms", 0) or 0) for s in steps]

    # Repetition is a cheap but useful proxy for stuck/looping agents.
    names = [str(s.get("name", "")) for s in steps]
    repeated = sum(max(0, c - 2) for c in {x: names.count(x) for x in set(names)}.values())

    return {
        "step_count": float(len(steps)),
        "tool_fraction": len(tool) / n,
        "error_rate": len(errors) / n,
        "tool_error_rate": len(tool_errors) / max(len(tool), 1),
        "llm_error_rate": len(llm_errors) / max(len(llm), 1),
        "repeat_rate": repeated / n,
        "duration_s": sum(durations) / 1000.0,
        "failure_markers": float(len(trace.get("failures") or [])),
    }

# Hand-calibrated bootstrap weights. train.py will eventually learn these.
_WEIGHTS = {
    "error_rate": -5.0,
    "tool_error_rate": -2.2,
    "llm_error_rate": -1.8,
    "repeat_rate": -2.4,
    "failure_markers": -1.3,
    "step_count": -0.018,
    "duration_s": -0.001,
}
_BIAS = 2.35

def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)

def predict(trace: dict[str, Any], model: dict[str, Any] | None = None) -> RiskPrediction:
    f = extract_features(trace)
    if model:
        names=model["feature_names"]; means=model["means"]; scales=model["scales"]; weights=model["weights"]
        z=[(f.get(k,0.0)-means[i])/scales[i] for i,k in enumerate(names)]
        raw=model["bias"]+sum(a*v for a,v in zip(weights,z))
        cal=model.get("calibration",{}); logit=cal.get("a",1.0)*raw+cal.get("b",0.0)
    else:
        logit = _BIAS + sum(_WEIGHTS[k] * f[k] for k in _WEIGHTS)
    p = max(0.01, min(0.99, _sigmoid(logit)))

    reasons: list[str] = []
    if f["tool_error_rate"] > 0:
        failure_type = "tool_execution"; reasons.append("tool execution errors observed")
    elif f["llm_error_rate"] > 0:
        failure_type = "model_execution"; reasons.append("model execution errors observed")
    elif f["repeat_rate"] >= 0.15:
        failure_type = "looping"; reasons.append("repeated steps suggest a loop")
    elif f["step_count"] >= 30 or f["duration_s"] >= 120:
        failure_type = "high_cost"; reasons.append("trajectory is long or slow")
    elif f["failure_markers"] > 0:
        failure_type = "unknown"; reasons.append("failure markers are present")
    else:
        failure_type = "none"; reasons.append("no strong failure signal observed")

    if p >= 0.75:
        action = "continue"
    elif p >= 0.50:
        action = "retry" if failure_type in ("tool_execution", "model_execution") else "continue"
    elif p >= 0.25:
        action = "replan"
    else:
        action = "escalate"

    # Confidence is about the risk decision, not task correctness.
    confidence = round(min(0.98, 0.55 + abs(p - 0.5) * 0.8 + min(f["step_count"], 20) / 100), 4)
    return RiskPrediction(round(p, 4), failure_type, confidence, action, f, reasons)
