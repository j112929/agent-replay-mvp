"""Common ingestion contract for external agent traces."""
from __future__ import annotations
import datetime as dt, hashlib, json
from typing import Any

def stable_id(value: Any) -> str:
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()[:24]

def envelope(value=None,present=True):
    return {"present":present,"value":value,"replayability":"complete"} if present else {"present":False,"replayability":"unsupported"}

def iso(ts):
    if isinstance(ts,str): return ts
    if isinstance(ts,(int,float)):
        # OTel timestamps are often ns since epoch.
        seconds=ts/1e9 if ts>1e14 else ts/1000 if ts>1e11 else ts
        return dt.datetime.fromtimestamp(seconds,dt.timezone.utc).isoformat()
    return dt.datetime.now(dt.timezone.utc).isoformat()

def duration_ms(start,end):
    if isinstance(start,(int,float)) and isinstance(end,(int,float)):
        scale=1e6 if max(start,end)>1e14 else 1
        return max(0,(end-start)/scale)
    return 0.0
