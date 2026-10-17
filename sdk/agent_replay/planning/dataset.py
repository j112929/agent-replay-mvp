"""Real transitions only; task-group splits and exact-context leakage checks."""
from dataclasses import asdict
from ..serialization import digest, canonical
from ..contracts import validate_v2
from ..security.sanitizer import sanitize_artifact
from .contracts import data


def fingerprint(row): return digest([row['history_context'],row['action'],row['environment_version']])


def extract(trace):
    trace=validate_v2(sanitize_artifact(trace))
    if trace.get('replay') or trace.get('privacy',{}).get('status')=='failed_closed': return []
    result=[];benchmark=trace.get('metadata',{}).get('benchmark',{})
    for step in trace['steps']:
        if step['kind']!='tool' or step['execution']['status']!='completed': continue
        if any(step.get(k,{}).get('replayability')!='complete' for k in ('input','output')): continue
        source=step['input'].get('value');actual=step['output'].get('value')
        if not isinstance(source,dict) or not isinstance(actual,dict) or not isinstance(source.get('state'),dict) or not isinstance(source.get('action'),dict) or not isinstance(actual.get('state_delta'),dict): continue
        if any(marker in canonical([source,actual]) for marker in ('[REDACTED]','[SANITIZATION_FAILED]')): continue
        action=source['action']
        result.append({'schema_version':'world-transition.v1','source_trajectory_id':trace['id'],
            'source_step_id':step['id'],'task_group':source.get('task_group',benchmark.get('task_id',trace['id'])),
            'history_context':data(source['state']),'action':{'tool_name':action['tool_name'],'action_payload':action['action_payload']},
            'actual_observation':data(actual),'actual_state_delta':data(actual['state_delta']),
            'task_outcome':trace.get('evaluation',{}).get('benchmark',{}).get('success'),
            'environment_version':source.get('environment_version','unknown'),'timestamp':trace['started_at'],
            'origin':'real_tool_observation'})
    return result


def validate_dataset(rows):
    if not isinstance(rows,list) or len(rows)>10000: raise ValueError('Dataset bound exceeded')
    for row in rows:
        if row.get('schema_version')!='world-transition.v1' or row.get('origin')!='real_tool_observation': raise ValueError('Real transition evidence required')
        for key in ('task_group','source_trajectory_id','source_step_id','environment_version','timestamp'):
            if not isinstance(row.get(key),str) or not row[key]: raise ValueError('Missing dataset provenance')
        if not all(isinstance(row.get(key),dict) for key in ('history_context','action','actual_observation','actual_state_delta')): raise ValueError('Invalid transition')
        data(row)
    return rows


def assert_disjoint(*datasets):
    seen_groups=set();seen_sources=set();seen_contexts=set()
    for rows in datasets:
        validate_dataset(rows)
        groups={r['task_group'] for r in rows};sources={r['source_trajectory_id'] for r in rows};contexts={fingerprint(r) for r in rows}
        if groups&seen_groups or sources&seen_sources or contexts&seen_contexts: raise ValueError('Held-out leakage: overlapping task, trajectory or exact transition context')
        seen_groups|=groups;seen_sources|=sources;seen_contexts|=contexts


def split(rows):
    validate_dataset(rows)
    groups=sorted({r['task_group'] for r in rows},key=lambda g:digest(g))
    if len(groups)<6: raise ValueError('At least six task groups required for train/validation/test')
    train=set(groups[:len(groups)*6//10]);validation=set(groups[len(groups)*6//10:len(groups)*8//10])
    datasets=([r for r in rows if r['task_group'] in train],
        [r for r in rows if r['task_group'] in validation],
        [r for r in rows if r['task_group'] not in train|validation])
    assert_disjoint(*datasets);return datasets


def retrieve(rows, context, action, k=8):
    # Never retrieve another transition from the evaluation task group itself.
    eligible=[r for r in rows if r['task_group']!=context.task_group and
        r['environment_version']==context.environment_version and r['action']=={'tool_name':action.tool_name,'action_payload':action.action_payload}]
    def score(row):
        # State flags are causal features; numeric workload provides secondary similarity.
        state=row['history_context']
        return sum(10 for key in ('tests_run','compat_checked') if state.get(key)==context.state.get(key))-abs(state.get('workload',0)-context.state.get('workload',0))
    if not eligible: return []
    best=max(score(r) for r in eligible)
    return sorted((r for r in eligible if score(r)>=best-.25),key=lambda r:-score(r))[:k]
