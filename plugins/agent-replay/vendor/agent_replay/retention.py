"""Explicit cleanup. Protect source artifacts and accepted baseline references."""
import time
from pathlib import Path
from .storage import read

def cleanup(directory,project_root,days=30,apply=False):
    if days<1:raise ValueError('Retention must be at least one day')
    protected=set()
    for path in Path(project_root).rglob('*.case.json'):
        try:
            case=read(path)
            for group,key in (('source','trace_path'),('baseline','result_path')):
                if case.get(group,{}).get(key):protected.add((path.parent/case[group][key]).resolve())
        except (ValueError,OSError):raise ValueError('Cannot safely determine case references: '+str(path))
    selected=[];cutoff=time.time()-days*86400
    for path in Path(directory).rglob('*.json'):
        if path.resolve() in protected or path.stat().st_mtime>=cutoff:continue
        # Only clean standalone traces, never jobs/specs/cases/journals.
        try:
            value=read(path)
            if isinstance(value,dict) and 'steps' in value and value.get('schema_version') in ('1.0','2.0'):selected.append(path)
        except (ValueError,OSError):continue
    result={'dry_run':not apply,'files':[str(p) for p in selected],'bytes':sum(p.stat().st_size for p in selected)}
    if apply:
        for p in selected:p.unlink()
    return result
