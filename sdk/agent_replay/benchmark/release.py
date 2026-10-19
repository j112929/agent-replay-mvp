"""Three-state release gates over the existing paired benchmark comparison."""
import math
from .compare import compare_runs
from .store import RunStore
from ..storage import read, within
from ..comparison import compare as compare_trajectories


def paired_statistics(comparison):
    rows=comparison.get('tasks',[])
    groups=[r['candidate']['metadata'].get('task_group',r['task_id']) for r in rows]
    if len(set(groups))!=len(groups):
        from ..probabilistic.experiments.statistics import paired_effect
        def records(which):
            return [{'task_group':row[which]['metadata'].get('task_group',row['task_id']),
                'trial_seed':row[which]['metadata'].get('probabilistic',{}).get('trial_seed',row['task_id']),
                'success':row[which]['success']} for row in rows]
        effect=paired_effect(records('baseline'),records('candidate'),'success')
        return {'paired_tasks':len(rows),'independent_groups':effect['independent_groups'],
            'one_sided_p_value':effect['p_value'],  # conservative two-sided cluster test
            'success_delta':effect['effect'],'approximate_95_percent_interval':effect['confidence_interval'],
            'limitations':effect['method']+'; conservative two-sided p-value for release regression'}
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
               'max_latency_increase','max_cost_increase','require_replay','inconclusive_action','planning','probabilistic'}
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
    probabilistic_coverage=True
    if 'probabilistic' in policy:
        pp=policy['probabilistic']
        if not isinstance(pp,dict):raise ValueError('Invalid probabilistic policy')
        required_samples,required_groups=pp.get('min_labeled_samples',200),pp.get('min_groups',20)
        if type(required_samples) is not int or type(required_groups) is not int or min(required_samples,required_groups)<2:
            raise ValueError('Invalid probabilistic coverage threshold')
        groups={r['candidate']['metadata'].get('task_group',r['task_id']) for r in comparison['tasks']}
        probabilistic_coverage=comparison['matched_count']>=required_samples and len(groups)>=required_groups
        if not probabilistic_coverage:uncertain.append('Insufficient probabilistic coverage; performance release decisions deferred')
    if comparison['matched_count'] < policy['min_tasks']:
        uncertain.append('Insufficient matched task count')
    if any(comparison[k] for k in ('added_tasks','removed_tasks','changed_tasks')):
        uncertain.append('Task sets or definitions changed')
    if any(comparison['infrastructure_errors'].values()):
        uncertain.append('Required tasks have infrastructure errors')
    rate=comparison['candidate']['success_rate']
    if rate is None:
        uncertain.append('Task success rate unavailable')
    elif comparison['matched_count'] >= policy['min_tasks'] and probabilistic_coverage:
        # Wilson upper bound: do not fail a minimum-rate SLO solely on sampling noise.
        n=comparison['matched_count'];z=1.96
        if 'independent_groups' in statistics:
            from ..probabilistic.experiments.statistics import bootstrap
            grouped=[{'task_group':r['candidate']['metadata'].get('task_group',r['task_id']),'success':r['candidate']['success']} for r in comparison['tasks']]
            ci=bootstrap(grouped,lambda rr:sum(r['success'] for r in rr)/len(rr))
            upper=ci[1] if ci else 1
        else:
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
        if not probabilistic_coverage:
            uncertain.append('Insufficient probabilistic coverage: '+metric);continue
        a,b=(comparison[k]['distributions'][metric] for k in ('baseline','candidate'))
        change=comparison['metrics'][metric]['mean']
        if a['missing'] or b['missing'] or not a['count'] or not b['count']:
            uncertain.append('Missing '+metric+' measurements')
        elif (change['baseline']==0 and change['candidate']>0) or (change['relative_change'] is not None and change['relative_change'] > policy[threshold]):
            # Descriptive means alone do not establish significance for noisy latency/cost.
            rows=comparison['tasks']; differences=[r['candidate'][metric]-(1+policy[threshold])*r['baseline'][metric] for r in rows]
            n=len(differences);avg=sum(differences)/n
            variance=sum((x-avg)**2 for x in differences)/(n-1) if n>1 else None
            if 'independent_groups' in statistics:
                from ..probabilistic.experiments.statistics import bootstrap
                grouped=[{'task_group':r['candidate']['metadata'].get('task_group',r['task_id']),'difference':d} for r,d in zip(rows,differences)]
                ci=bootstrap(grouped,lambda rr:sum(r['difference'] for r in rr)/len(rr));lower=ci[0] if ci else None
            else:lower=avg-1.96*math.sqrt(variance/n) if variance is not None else None
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
    result={'decision':decision,'blocking':decision=='FAIL' or decision=='INCONCLUSIVE' and policy['inconclusive_action']=='block',
        'reasons':failures+uncertain,'policy':policy,'statistics':statistics,'comparison':comparison,
        'versions':{'baseline':a['agent_config'],'candidate':b['agent_config']}}
    if 'planning' in policy:
        from ..planning.gates import apply_gate
        result=apply_gate(result,baseline,candidate,policy['planning'])
    if 'probabilistic' in policy:
        from ..probabilistic.gates import apply_gate
        result=apply_gate(result,baseline,candidate,policy['probabilistic'])
    return result


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
