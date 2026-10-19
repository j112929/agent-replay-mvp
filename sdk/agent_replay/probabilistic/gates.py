"""Opt-in probabilistic evidence extension to the existing release quality gate."""
from statistics import mean
from ..benchmark.store import RunStore
from ..storage import read
from .calibration.collector import join_outcomes
from .experiments.statistics import bootstrap, holm, paired_effect


def _load(directory):
    run,results=RunStore(directory).load()
    evidence=read(RunStore(directory).directory/'reliability.json')
    if evidence.get('schema_version')!='research-evidence.v1' or evidence.get('run_id')!=run['id'] or evidence.get('suite_digest')!=run['suite_digest']:
        raise ValueError('Research run provenance mismatch')
    # Reconstruct labels and forecasts from persisted verifier results, not supplied metrics.
    joined=join_outcomes([f for r in results for f in r.metadata['probabilistic']['forecasts']],
                         [r.metadata['probabilistic']['outcome'] for r in results])
    fc={r['task_id']:r for r in joined['rows'] if r['source']=='world_model_mc'}
    rows=[]
    for r in results:
        p=r.metadata['probabilistic'];label=p['outcome']
        if label['run_id']!=run['id'] or label['trajectory_id']!=r.trajectory_id or label['task_id']!=r.task_id:
            raise ValueError('Verifier label reference mismatch')
        if label['status']=='verified' and label['success']!=r.success:
            raise ValueError('Persisted benchmark and outcome labels disagree')
        rows.append({'task_group':r.metadata['task_group'],'trial_seed':p['trial_seed'],'task_id':r.task_id,
            'success':r.success if label['status']=='verified' else None,'severe_failure':p['severe_failure'] if label['status']=='verified' else None,
            'brier_score':(fc[r.task_id]['probability']-fc[r.task_id]['outcome'])**2 if r.task_id in fc else None})
    return run,evidence,rows


def apply_gate(gate,baseline,candidate,policy):
    allowed={'baseline_run_id','min_labeled_samples','min_groups','max_brier_regression','max_severe_failure_regression','max_success_rate_regression'}
    if not isinstance(policy,dict) or set(policy)-allowed or not isinstance(policy.get('baseline_run_id'),str):
        raise ValueError('Probabilistic gate requires explicit baseline_run_id and valid rules')
    for name in ('min_labeled_samples','min_groups'):
        value=policy.get(name,200 if name=='min_labeled_samples' else 20)
        if type(value) is not int or value<2:raise ValueError('Invalid probabilistic coverage threshold')
    from ..planning.contracts import finite
    for name,value in policy.items():
        if name.startswith('max_'):finite(value,name,0,1)
    failures=[];uncertain=[];statistics={}
    try:
        a,ae,aa=_load(baseline);b,be,bb=_load(candidate)
        if a['id']!=policy['baseline_run_id'] or ae['benchmark_version']!=be['benchmark_version'] or ae['evaluation_protocol']!=be['evaluation_protocol']:
            raise ValueError('Explicit baseline or evaluation protocol mismatch')
        for name,field,sign in (('max_brier_regression','brier_score',1),('max_severe_failure_regression','severe_failure',1),('max_success_rate_regression','success',-1)):
            if name not in policy:continue
            try:effect=paired_effect(aa,bb,field,min_groups=policy.get('min_groups',20))
            except ValueError:
                uncertain.append('Missing, invalid or unmatched required probabilistic evidence: '+name);continue
            diffs=[]
            index={(r['task_group'],r['trial_seed']):r for r in aa}
            for r in bb:
                prior=index[(r['task_group'],r['trial_seed'])]
                diffs.append({'task_group':r['task_group'],'excess':sign*(r[field]-prior[field])-policy[name]})
            ci=bootstrap(diffs,lambda rr:mean(r['excess'] for r in rr))
            statistics[name]={**effect,'threshold':policy[name],'mean_excess':mean(r['excess'] for r in diffs),'excess_95_interval':ci}
            if effect['paired_samples']<policy.get('min_labeled_samples',200) or effect['independent_groups']<policy.get('min_groups',20) or ci is None:
                uncertain.append('Insufficient independent probabilistic coverage: '+name)
            elif ci[0]>0:failures.append(name)
            elif ci[1]>0:uncertain.append('Probabilistic threshold statistically uncertain: '+name)
        if not any(name.startswith('max_') for name in policy):uncertain.append('No probabilistic metric rule configured')
        # Require corrected paired evidence before calling multiple regressions FAIL.
        names=list(statistics)
        for name,p in zip(names,holm([statistics[k]['p_value'] for k in names])):
            statistics[name]['holm_p_value']=p
            if name in failures and p>.05:
                failures.remove(name);uncertain.append('Corrected probabilistic significance inconclusive: '+name)
    except (ValueError,OSError,KeyError,TypeError):
        uncertain.append('Invalid, missing or mismatched probabilistic run evidence')
    decision='FAIL' if failures or gate['decision']=='FAIL' else 'INCONCLUSIVE' if uncertain or gate['decision']=='INCONCLUSIVE' else 'PASS'
    return {**gate,'decision':decision,'blocking':decision=='FAIL' or decision=='INCONCLUSIVE' and gate['policy']['inconclusive_action']=='block',
        'reasons':gate['reasons']+['Probabilistic regression: '+name for name in failures]+uncertain,
        'probabilistic_evidence':statistics,'probabilistic_policy':policy}
