import uuid
from ..migrations import to_v2
from ..serialization import MISSING,digest
from .align import align

def changes(a,b,path='',rows=None):
    rows=[] if rows is None else rows
    if a is not MISSING and b is not MISSING and type(a)==type(b) and a==b:return rows
    if isinstance(a,dict) and isinstance(b,dict):
        for k in sorted(set(a)|set(b)):
            changes(a.get(k,MISSING),b.get(k,MISSING),path+'/'+k.replace('~','~0').replace('/','~1'),rows)
    elif isinstance(a,list) and isinstance(b,list):
        for i in range(max(len(a),len(b))):changes(a[i] if i<len(a) else MISSING,b[i] if i<len(b) else MISSING,path+'/'+str(i),rows)
    else: rows.append({'path':path,'before_present':a is not MISSING,'after_present':b is not MISSING,'before':None if a is MISSING else a,'after':None if b is MISSING else b})
    return rows

def compare(original,candidate,ignore_paths=None):
    a,b=to_v2(original),to_v2(candidate);pairs,added,removed,ambiguous=align(a['steps'],b['steps']);aligned=[];diffs=[]
    ignore_paths=ignore_paths or []
    for left,right,confidence in pairs:
        fields=('input','output','error','model','tool')
        before={k:left[k] for k in fields if k in left};after={k:right[k] for k in fields if k in right}
        before['status']=left['execution']['status'];after['status']=right['execution']['status']
        rows=[r for r in changes(before,after) if r['path'] not in ignore_paths]
        aligned.append({'source_id':left['id'],'candidate_id':right['id'],'confidence':confidence,'changes':rows})
        if rows:diffs.append({'source_id':left['id'],'candidate_id':right['id'],'changes':rows})
    return {'schema_version':'1.0','id':str(uuid.uuid4()),'source_trace_id':a['id'],'candidate_trace_id':b['id'],'source_digest':digest(original),'candidate_digest':digest(candidate),
            'aligned_pairs':aligned,'added_steps':added,'removed_steps':removed,'ambiguous_groups':ambiguous,'field_changes':diffs,
            'first_observed_divergence':diffs[0] if diffs else ({'added_steps':added,'removed_steps':removed} if added or removed else None),
            'output_changes':changes(a['output'],b['output']), 'execution':{'source':a['execution']['status'],'candidate':b['execution']['status']},
            'coverage':{'source_steps':len(a['steps']),'candidate_steps':len(b['steps']),'aligned':len(pairs)},'ignore_paths':ignore_paths,
            'note':'First observable divergence is evidence, not a root-cause claim. Concurrent branches may be unordered.'}
