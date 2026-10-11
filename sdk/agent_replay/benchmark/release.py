"""Three-state release gates over the existing paired benchmark comparison."""
import math
from .compare import compare_runs
from .store import RunStore
from ..storage import read, within
from ..comparison import compare as compare_trajectories


def paired_statistics(comparison):
    improvements, regressions = len(comparison['newly_passing']), len(comparison['newly_failing'])
    discordant = improvements + regressions
    # One-sided exact McNemar/binomial: candidate failures exceed improvements.
    p = sum(math.comb(discordant, k) for k in range(regressions, discordant + 1)) / 2**discordant if discordant else 1.0
    n = comparison['matched_count']
    delta = (improvements - regressions) / n if n else None
    se = math.sqrt(max(0, discordant / n - delta**2) / n) if n else None
    return {'paired_tasks': n, 'discordant_pairs': discordant, 'one_sided_p_value': p,
            'success_delta': delta, 'approximate_95_percent_interval':
            [max(-1, delta - 1.96*se), min(1, delta + 1.96*se)] if n else None,
            'limitations': 'One observation per matched task; interval is descriptive. Repeated model trials are not inferred.'}


def release_gate(baseline, candidate, policy=None):
    policy = dict(policy or {})
    allowed = {'environment','min_tasks','alpha','min_success_rate','max_success_drop','critical_tags',
               'max_latency_increase','max_cost_increase','require_replay','inconclusive_action'}
    if set(policy) - allowed:
        raise ValueError('Unknown release policy fields')
    environment = policy.get('environment', 'PRODUCTION')
    if environment not in ('DEVELOPMENT','STAGING','PRODUCTION'):
        raise ValueError('Unknown release environment')
    defaults = {'min_tasks': 20, 'alpha': .05, 'min_success_rate': 0,
        'max_success_drop': 0, 'critical_tags': ['critical'], 'require_replay': True,
        'inconclusive_action': 'allow' if environment == 'DEVELOPMENT' else 'block'}
    policy = {**defaults, **policy, 'environment': environment}
    for field in ('alpha', 'min_success_rate', 'max_success_drop'):
        if type(policy[field]) not in (int,float) or not math.isfinite(policy[field]) or not 0 <= policy[field] <= 1:
            raise ValueError('Invalid release threshold: '+field)
    if type(policy['min_tasks']) is not int or policy['min_tasks'] < 1:
        raise ValueError('min_tasks must be a positive integer')
    for field in ('max_latency_increase','max_cost_increase'):
        value=policy.get(field)
        if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or value < 0):
            raise ValueError('Invalid release threshold: '+field)
    if policy['inconclusive_action'] not in ('allow','block') or type(policy['require_replay']) is not bool:
        raise ValueError('Invalid release decision policy')
    if not isinstance(policy['critical_tags'], list) or any(not isinstance(t,str) for t in policy['critical_tags']):
        raise ValueError('critical_tags must be strings')
    uncertain, failures = [], []
    try:
        comparison = compare_runs(baseline,candidate)
    except (ValueError,OSError,KeyError,TypeError):
        return {'decision':'INCONCLUSIVE','blocking':policy['inconclusive_action']=='block','policy':policy,
                'reasons':['Missing, invalid or incomplete benchmark evidence'], 'comparison':None}
    statistics = paired_statistics(comparison)
    if comparison['matched_count'] < policy['min_tasks']:
        uncertain.append('Insufficient matched task count')
    if any(comparison[k] for k in ('added_tasks','removed_tasks','changed_tasks')):
        uncertain.append('Task sets or definitions changed')
    if any(comparison['infrastructure_errors'].values()):
        uncertain.append('Required tasks have infrastructure errors')
    rate=comparison['candidate']['success_rate']
    if rate is None:
        uncertain.append('Task success rate unavailable')
    elif comparison['matched_count'] >= policy['min_tasks']:
        # Wilson upper bound: do not fail a minimum-rate SLO solely on sampling noise.
        n=comparison['matched_count'];z=1.96
        upper=(rate+z*z/(2*n)+z*math.sqrt(rate*(1-rate)/n+z*z/(4*n*n)))/(1+z*z/n)
        if rate < policy['min_success_rate']:
            (failures if upper < policy['min_success_rate'] else uncertain).append('Minimum task success rate not established')
        drop = comparison['metrics']['success_rate']['delta']
        if drop < -policy['max_success_drop']-1e-12:
            (failures if statistics['one_sided_p_value'] <= policy['alpha'] else uncertain).append('Paired success regression exceeds policy')
    _, candidates = RunStore(candidate).load()
    for r in candidates:
        if not r.success and set(r.tags) & set(policy['critical_tags']):
            failures.append('Critical task failed: '+r.task_id)
    for metric, threshold in (('duration_ms','max_latency_increase'),('cost_usd','max_cost_increase')):
        if threshold not in policy or policy[threshold] is None: continue
        a,b=(comparison[k]['distributions'][metric] for k in ('baseline','candidate'))
        change=comparison['metrics'][metric]['mean']
        if a['missing'] or b['missing'] or not a['count'] or not b['count']:
            uncertain.append('Missing '+metric+' measurements')
        elif (change['baseline']==0 and change['candidate']>0) or (change['relative_change'] is not None and change['relative_change'] > policy[threshold]):
            # Descriptive means alone do not establish significance for noisy latency/cost.
            rows=comparison['tasks']; differences=[r['candidate'][metric]-(1+policy[threshold])*r['baseline'][metric] for r in rows]
            n=len(differences);avg=sum(differences)/n
            variance=sum((x-avg)**2 for x in differences)/(n-1) if n>1 else None
            lower=avg-1.96*math.sqrt(variance/n) if variance is not None else None
            (failures if n>=policy['min_tasks'] and lower is not None and lower>0 else uncertain).append(metric+' regression exceeds policy')
    if policy['require_replay']:
        for r in candidates:
            try:
                trace=read(within(candidate,r.trajectory_path))
                if trace.get('privacy',{}).get('status')=='failed_closed' or any(
                    s.get('output',{}).get('replayability')!='complete' for s in trace.get('steps',[]) if s['kind']=='tool'):
                    uncertain.append('Reusable replay evidence unavailable: '+r.task_id)
            except (ValueError,OSError,TypeError,KeyError):
                uncertain.append('Trajectory evidence unavailable: '+r.task_id)
    decision='FAIL' if failures else 'INCONCLUSIVE' if uncertain else 'PASS'
    a,_=RunStore(baseline).load();b,_=RunStore(candidate).load()
    return {'decision':decision,'blocking':decision=='FAIL' or decision=='INCONCLUSIVE' and policy['inconclusive_action']=='block',
        'reasons':failures+uncertain,'policy':policy,'statistics':statistics,'comparison':comparison,
        'versions':{'baseline':a['agent_config'],'candidate':b['agent_config']}}


def failure_analysis(baseline,candidate):
    comparison=compare_runs(baseline,candidate)
    rows=[]
    for task in comparison['tasks']:
        if task['candidate']['success']: continue
        item={'task_id':task['task_id'],'classification':task['classification'],
              'failure_category':task['candidate']['failure_category'],
              'trajectory_path':task['candidate']['trajectory_path']}
        try:
            a=read(within(baseline,task['baseline']['trajectory_path']))
            b=read(within(candidate,task['candidate']['trajectory_path']))
            item.update(behavior_changes=compare_trajectories(a,b),privacy=b.get('privacy',{}),
                        replay={'trace_playback':True,'sandbox_execution':False,
                        'limitations':b.get('privacy',{}).get('limitations',[])})
        except (ValueError,OSError,TypeError,KeyError):
            item['replay']={'trace_playback':False,'reason':'Trajectory evidence unavailable'}
        rows.append(item)
    return rows
