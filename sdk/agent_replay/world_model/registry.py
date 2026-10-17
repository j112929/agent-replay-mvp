"""Offline evidence-gated model references, reusing existing content-addressed artifacts."""
from pathlib import Path
import math
from ..rollout.artifacts import LocalObjects,verify_checkpoint
from ..storage import read,save,within
from ..serialization import digest
from ..benchmark.store import RunStore


def shadow_evidence(directory,model_version):
    run,results=RunStore(directory).load();seen=[]
    if run['agent_config']['parameters'].get('planning_mode')!='shadow':raise ValueError('Real shadow benchmark evidence required')
    for result in results:
        trace=read(within(directory,result.trajectory_path));planning=trace.get('metadata',{}).get('planning',{})
        if planning.get('mode')!='shadow' or not planning.get('sessions'):raise ValueError('Missing shadow decisions')
        for session in planning['sessions']:
            if session.get('world_model',{}).get('version')!=model_version or session['status']=='failed':raise ValueError('Shadow model/version unavailable')
            if session.get('real_execution') and session['real_execution']['candidate_id']!=session['executed_baseline_action']['candidate_id']:raise ValueError('Shadow changed executed policy')
        seen.append(trace.get('metadata',{}).get('world_model',{}).get('scenario_group',result.task_id))
    return {'run_id':run['id'],'model_version':model_version,'tasks':len(results),'groups':sorted(set(seen)),
        'unsafe_actions':sum(r.metadata.get('planning',{}).get('unsafe_action_count',0) for r in results),
        'source':'verified_shadow_run','run_digest':digest(run)}


def promote(reference,checkpoint_root,evaluation,shadow_run,policy,registry,baseline=None):
    required={'min_exact_match','max_prediction_failure_rate','max_latency_ms','max_cost_per_prediction','min_heldout_groups','min_shadow_tasks','max_exact_match_drop','max_brier_increase'}
    if not isinstance(policy,dict) or set(policy)!=required or any(type(v) not in (float,int) or not math.isfinite(v) or v<0 for v in policy.values()):raise ValueError('Explicit finite promotion thresholds required')
    directory=verify_checkpoint(reference,checkpoint_root)
    model=read(directory/'model.json') if (directory/'model.json').exists() else {**read(directory/'world-model.json'),'version':reference['manifest_digest']}
    if evaluation.get('schema_version')!='world-evaluation.v1' or evaluation['model_version']!=model['version']:raise ValueError('Evaluation model mismatch')
    if set(model['training_groups'])&set(evaluation['task_groups']):raise ValueError('Evaluation not held out')
    summary=evaluation['summary'];reasons=[]
    if not isinstance(baseline,dict) or baseline.get('schema_version')!='world-evaluation.v1' or baseline.get('dataset_digest')!=evaluation.get('dataset_digest'):raise ValueError('Paired held-out baseline evaluation required')
    before=baseline['summary'];a=before.get('exact_match');b=summary.get('exact_match')
    if a is None or b is None or a-b>policy['max_exact_match_drop']:reasons.append('Held-out exact-match regression')
    a=before.get('calibration',{}).get('brier_score');b=summary.get('calibration',{}).get('brier_score')
    if a is None or b is None or b-a>policy['max_brier_increase']:reasons.append('Calibration evidence unavailable or regressed')
    if len(evaluation['task_groups'])<policy['min_heldout_groups']:reasons.append('Insufficient held-out groups')
    for name,threshold,minimum in [('exact_match','min_exact_match',True),('prediction_failure_rate','max_prediction_failure_rate',False),('latency_ms','max_latency_ms',False),('cost_per_prediction','max_cost_per_prediction',False)]:
        v=summary.get(name)
        if v is None or (v<policy[threshold] if minimum else v>policy[threshold]):reasons.append('Prediction promotion threshold: '+name)
    shadow=shadow_evidence(shadow_run,model['version'])
    if shadow['tasks']<policy['min_shadow_tasks'] or shadow['unsafe_actions'] or set(shadow['groups'])&set(model['training_groups']):reasons.append('Shadow safety/coverage/independence check failed')
    if reasons:raise ValueError('; '.join(reasons))
    root=Path(registry);objects=LocalObjects(root/'objects')
    record={'schema_version':'world-model-registry.v1','model_version':model['version'],'checkpoint':reference,
        'evaluation_digest':digest(evaluation),'baseline_digest':digest(baseline),'shadow':shadow,'policy':policy,'stage':'active_eligible','deployment':'offline_reference_only'}
    key=objects.put(record);save(root/'active.json',{'object':key});return record


def rollback(registry,key):
    root=Path(registry);value=LocalObjects(root/'objects').get(key)
    if value.get('schema_version')!='world-model-registry.v1' or value.get('stage')!='active_eligible':raise ValueError('Unknown approved model reference')
    save(root/'active.json',{'object':key});return value
