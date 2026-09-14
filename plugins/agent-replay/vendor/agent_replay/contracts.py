"""Runtime validation shared by import, replay, and CI."""
import datetime
from .serialization import canonical

def validate_v2(t):
    canonical(t)
    if not isinstance(t,dict) or t.get('schema_version')!='2.0': raise ValueError('Expected trajectory v2')
    for k in ('id','name','started_at'):
        if not isinstance(t.get(k),str) or not t[k].strip() or len(t[k])>240: raise ValueError('Invalid '+k)
    datetime.datetime.fromisoformat(t['started_at'].replace('Z','+00:00'))
    def execution(e):
        if not isinstance(e,dict) or e.get('status') not in ('running','completed','error','interrupted'): raise ValueError('Invalid execution')
        for k in ('duration_ms','start_ms'):
            if k in e and (isinstance(e[k],bool) or not isinstance(e[k],(int,float)) or e[k]<0): raise ValueError('Invalid timing')
        if 'duration_ms' not in e: raise ValueError('Missing duration')
    def value(v):
        if not isinstance(v,dict) or type(v.get('present')) is not bool or v.get('replayability') not in ('complete','redacted','truncated','unsupported'): raise ValueError('Invalid value envelope')
        if v['present'] and 'value' not in v: raise ValueError('Present value is missing')
    execution(t.get('execution')); value(t.get('input')); value(t.get('output'))
    if not isinstance(t.get('steps'),list) or len(t['steps'])>2000: raise ValueError('Invalid steps')
    ids={}
    for s in t['steps']:
        if not isinstance(s,dict) or not isinstance(s.get('id'),str) or not s['id'] or s['id'] in ids: raise ValueError('Invalid or duplicate step ID')
        if s.get('kind') not in ('agent','llm','tool') or not isinstance(s.get('name'),str): raise ValueError('Invalid step')
        execution(s.get('execution')); value(s.get('input')); value(s.get('output')); ids[s['id']]=s
    for s in ids.values():
        seen={s['id']}; p=s.get('parent_id')
        while p:
            if p not in ids or p in seen: raise ValueError('Invalid parent reference or cycle')
            seen.add(p); p=ids[p].get('parent_id')
    return t
