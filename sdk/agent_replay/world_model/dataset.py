"""Versioned supervised export over existing real-transition records, no second store."""
import json
from pathlib import Path
from ..planning.dataset import extract,validate_dataset,assert_disjoint
from ..planning.contracts import data
from ..serialization import digest,canonical
from ..storage import read,save,within


def transitions(trace, adapter=None, sensitive_fields=()):
    from ..security.sanitizer import Sanitizer
    cleaner=Sanitizer(policy='STRICT',sensitive_fields=sensitive_fields);trace=cleaner.sanitize(trace)
    if cleaner.failed:raise ValueError('Training sanitization failed closed')
    from ..migrations import to_v2
    if trace.get('schema_version')=='1.0':trace=to_v2(trace)
    rows=extract(trace)
    if adapter is not None and adapter!='legacy-refund': raise ValueError('Unknown trusted transition adapter')
    # Legacy imports require explicit adapter; never infer execution from arbitrary text.
    if adapter=='legacy-refund' and not rows:
        from ..contracts import validate_v2
        from ..security.sanitizer import sanitize_artifact
        trace=validate_v2(sanitize_artifact(trace))
        if trace.get('replay'):return []
        state={};rows=[]
        for step in trace['steps']:
            if step['kind']!='tool' or step['execution']['status']!='completed':continue
            if step.get('boundary_id',step['name']) not in ('orders.lookup','payments.refund'):continue
            if any(step[k].get('replayability')!='complete' for k in ('input','output')):continue
            payload=step['input']['value'];out=step['output']['value']
            if not isinstance(payload,dict) or not isinstance(out,dict):continue
            row={'schema_version':'world-transition.v1','origin':'real_tool_observation','source_trajectory_id':trace['id'],
                'source_step_id':step['id'],'task_group':trace.get('metadata',{}).get('scenario_id',trace['id']),
                'history_context':dict(state),'action':{'tool_name':step['name'],'action_payload':payload},
                'actual_observation':out,'actual_state_delta':{},'state_delta_available':False,'history_context_available':False,'environment_version':'legacy-refund-observed.v1',
                'timestamp':trace['started_at'],'task_outcome':None}
            rows.append(row);state={**state,'last_observation':out}
    step_by_id={s['id']:s for s in trace['steps']};result=[];history=[]
    benchmark=trace.get('metadata',{}).get('benchmark',{})
    for row in rows:
        step=step_by_id[row['source_step_id']];source=step['input'].get('value',{})
        if source.get('training_authorized') is False or row['actual_observation'].get('unsafe') is True:continue
        item={**row,'task_id':benchmark.get('task_id',trace['id']),'step_index':trace['steps'].index(step),
            'history':list(history),'actual_reward':row['actual_observation'].get('reward'),
            'terminal':row['actual_observation'].get('terminal'),
            'tool_schema_version':source.get('tool_schema_version','unknown'),
            'provenance':{'source':'real_execution','adapter':adapter or 'native-structured','training_authorized':source.get('training_authorized',False)}}
        # Export only explicitly authorized traces, unless the operator chose the legacy adapter.
        if not item['provenance']['training_authorized'] and not trace.get('metadata',{}).get('world_model',{}).get('training_authorized') and adapter!='legacy-refund':continue
        item['provenance']['training_authorized']=True
        item['task_group']=trace.get('metadata',{}).get('world_model',{}).get('scenario_group',item['task_group'])
        if '[REDACTED]' in canonical(item) or '[SANITIZATION_FAILED]' in canonical(item):continue
        result.append(data(item));history.append({'action':row['action'],'observation':row['actual_observation']})
    return result


def build(traces, output, adapter=None, authorized=False, sensitive_fields=()):
    if authorized is not True:raise ValueError('Explicit operator training-data authorization required')
    root=Path(output);root.mkdir(parents=True,exist_ok=False)
    rows=[];seen=set()
    for trace in traces:
        for row in transitions(trace,adapter,sensitive_fields):
            key=digest([row['history_context'],row['history'],row['action'],row['actual_observation'],row['environment_version']])
            if key not in seen:seen.add(key);rows.append(row)
    if not rows:raise ValueError('No authorized real transitions available')
    rows.sort(key=lambda r:(r['source_trajectory_id'],r.get('step_index',0)))
    validate_dataset(rows)
    # Connected provenance groups: a source trajectory and a declared scenario never split.
    groups={r['task_group'] for r in rows};ordered=sorted(groups,key=digest)
    if len(groups)<6:raise ValueError('Six independent scenario groups required')
    a=set(ordered[:len(ordered)*6//10]);b=set(ordered[len(ordered)*6//10:len(ordered)*8//10])
    partitions={'train':[r for r in rows if r['task_group'] in a],'validation':[r for r in rows if r['task_group'] in b],
        'test':[r for r in rows if r['task_group'] not in a|b]}
    assert_disjoint(*partitions.values())
    files={}
    for name,values in partitions.items():
        payload=''.join(canonical(r)+'\n' for r in values)
        path=root/(name+'.jsonl');path.write_text(payload);path.chmod(0o600)
        files[name]={'file':name+'.jsonl','sha256':__import__('hashlib').sha256(payload.encode()).hexdigest(),'rows':len(values),'groups':sorted({r['task_group'] for r in values})}
    manifest={'schema_version':'world-dataset.v1','version':digest(rows),'splits':files,'rows':len(rows),'format':'jsonl',
        'environment_versions':sorted({r['environment_version'] for r in rows}),
        'limitations':['Scenario/family grouping must be supplied correctly; semantic near-duplicate detection is not automatic']}
    save(root/'dataset.json',manifest);return manifest


def load(directory,split):
    if split not in ('train','validation','test'):raise ValueError('Unknown dataset split')
    root=Path(directory);manifest=read(root/'dataset.json');item=manifest['splits'][split]
    if manifest['schema_version']!='world-dataset.v1':raise ValueError('Unknown dataset schema')
    path=within(root,item['file']);raw=path.read_bytes()
    if len(raw)>32*1024*1024 or __import__('hashlib').sha256(raw).hexdigest()!=item['sha256']:raise ValueError('Dataset integrity failure')
    rows=[json.loads(line) for line in raw.decode().splitlines()]
    validate_dataset(rows)
    if len(rows)!=item['rows']:raise ValueError('Dataset count mismatch')
    return manifest,rows
