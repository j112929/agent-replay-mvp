"""Normalize OpenTelemetry/OpenInference-style spans into trajectory.v2."""
from __future__ import annotations
from typing import Any
from .base import stable_id,envelope,iso,duration_ms

def _attrs(span):
    a=span.get("attributes") or {}
    if isinstance(a,list): return {x.get("key"):x.get("value") for x in a if isinstance(x,dict)}
    return a

def _kind(span):
    a=_attrs(span);k=str(a.get("openinference.span.kind") or a.get("gen_ai.operation.name") or span.get("name","")).lower()
    if any(x in k for x in ("llm","chat","completion","generate")):return "llm"
    if any(x in k for x in ("tool","function")):return "tool"
    return "agent"

def _status(span):
    s=span.get("status") or {};code=str(s.get("code") if isinstance(s,dict) else s).lower()
    return "error" if "error" in code else "completed"

def normalize(spans:list[dict[str,Any]], *, source="otel", raw_ref=None):
    if not spans:raise ValueError("At least one span is required")
    ids={str(s.get("spanId") or s.get("span_id") or stable_id(s)):s for s in spans}
    roots=[(i,s) for i,s in ids.items() if not str(s.get("parentSpanId") or s.get("parent_span_id") or "") in ids]
    root_id,root=min(roots or list(ids.items()),key=lambda x:x[1].get("startTimeUnixNano") or x[1].get("start_time") or 0)
    def start(s):return s.get("startTimeUnixNano") or s.get("start_time") or 0
    ordered=sorted(ids.items(),key=lambda x:start(x[1]))
    steps=[]
    for seq,(sid,s) in enumerate(ordered):
        if sid==root_id and len(ordered)>1:continue
        a=_attrs(s);parent=str(s.get("parentSpanId") or s.get("parent_span_id") or "") or None
        steps.append({"id":sid,"name":str(s.get("name") or a.get("openinference.span.name") or "span")[:240],
          "kind":_kind(s),"parent_id":parent if parent!=root_id else None,"seq":seq,
          "execution":{"status":_status(s),"start_ms":0,"duration_ms":duration_ms(start(s),s.get("endTimeUnixNano") or s.get("end_time") or start(s))},
          "input":envelope(a.get("input.value") or a.get("openinference.input.value"),a.get("input.value") is not None or a.get("openinference.input.value") is not None),
          "output":envelope(a.get("output.value") or a.get("openinference.output.value"),a.get("output.value") is not None or a.get("openinference.output.value") is not None),
          "external_attributes":a})
    status="error" if any(x["execution"]["status"]=="error" for x in steps) else "completed"
    return {"schema_version":"2.0","id":str(root.get("traceId") or root.get("trace_id") or stable_id(spans)),
      "name":str(root.get("name") or "external-agent-run")[:240],"started_at":iso(start(root)),
      "execution":{"status":status,"duration_ms":duration_ms(start(root),root.get("endTimeUnixNano") or root.get("end_time") or start(root))},
      "input":envelope(None,False),"output":envelope(None,False),"steps":steps,
      "provenance":{"ingestion":{"source":source,"raw_ref":raw_ref,"raw_preserved":raw_ref is not None}}}
