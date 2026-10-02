"""Raw external trace persistence + normalized derived trajectory."""
from pathlib import Path
import json,uuid
from ..storage import save
from ..schema import validate_trace
from .otel import normalize

def import_spans(spans,directory,source="otel"):
    root=Path(directory);raw_dir=root/"raw";trace_dir=root/"traces";raw_dir.mkdir(parents=True,exist_ok=True);trace_dir.mkdir(parents=True,exist_ok=True)
    rid=str(uuid.uuid4());raw_path=raw_dir/(rid+".json");save(raw_path,{"source":source,"spans":spans})
    trace=normalize(spans,source=source,raw_ref=str(raw_path.relative_to(root)))
    validate_trace(trace);path=save(trace_dir/(trace["id"]+".json"),trace)
    return {"trace":trace,"trace_path":str(path),"raw_path":str(raw_path)}
