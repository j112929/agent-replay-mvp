"""Non-destructive v1 imports; missing history remains unknown."""
import copy
from .serialization import envelope, MISSING

def to_v2(trace):
    if trace.get('schema_version') == '2.0':
        return copy.deepcopy(trace)
    t = copy.deepcopy(trace)
    result = {k: t[k] for k in ('id','name','started_at','tags','metadata','replay') if k in t}
    result.update(schema_version='2.0', execution={'status': {'success':'completed','error':'error','running':'running'}[t['status']], 'duration_ms': t['duration_ms']},
                  input=envelope(t.get('input', MISSING)), output=envelope(t.get('output', MISSING)), steps=[], failures=t.get('failures', []),
                  provenance=t.get('provenance', {'code': {'commit': None, 'dirty': None, 'source_available': False}}),
                  capture_health=t.get('capture_health', {'state':'partial','gaps':['Imported v1: environment and boundary coverage may be unknown']}),
                  evaluation={'verdict':'not_evaluated'})
    if t.get('error'): result['execution']['error'] = t['error']
    counts = {}
    ids = {s['id'] for s in t['steps']}
    for n, s in enumerate(t['steps']):
        scope = s.get('scope_path', [])
        key = (s.get('boundary_id', s['name']), tuple(scope), s.get('lane_id'))
        counts[key] = counts.get(key, 0)+1
        step = {k:v for k,v in s.items() if k not in ('status','start_ms','duration_ms','input','output','model')}
        step.update(seq=n+1, parent_id=s.get('parent_id') if s.get('parent_id') in ids else None,
                    scope_path=scope, occurrence=s.get('occurrence', counts[key]), attempt=s.get('attempt', 1),
                    execution={'status': {'success':'completed','error':'error','running':'running'}[s['status']], 'start_ms':s['start_ms'], 'duration_ms':s['duration_ms']},
                    input=envelope(s.get('input', MISSING)), output=envelope(s.get('output', MISSING)))
        if s.get('model'): step['model'] = {'provider':s.get('provider_kind','openai_compatible'),'requested':s['model']}
        result['steps'].append(step)
    if not t.get('provenance'): result['migrated_from'] = '1.0'
    if 'fidelity' in t: result['fidelity'] = t['fidelity']
    return result

def legacy(trace):
    if trace.get('schema_version') == '1.0': return copy.deepcopy(trace)
    t = copy.deepcopy(trace)
    e = t.pop('execution')
    t.update(schema_version='1.0', status={'completed':'success','interrupted':'error'}.get(e['status'],e['status']), duration_ms=e['duration_ms'])
    if e.get('error'): t['error'] = e['error']
    for k in ('input','output'):
        value=t.pop(k,{})
        if value.get('present'): t[k]=value['value']
    for s in t['steps']:
        e=s.pop('execution')
        s.update(status={'completed':'success','interrupted':'error'}.get(e['status'],e['status']), start_ms=e['start_ms'],duration_ms=e['duration_ms'])
        for k in ('input','output'):
            value=s.pop(k,{})
            s['_'+k+'_replayability']=value.get('replayability','unsupported')
            if value.get('present'): s[k]=value['value']
        if isinstance(s.get('model'),dict): s['model']=s['model']['requested']
    return t
