"""Planning extension of the existing paired release gate, blocking inconclusive."""
import math
from statistics import mean
from .metrics import interval
from ..benchmark.compare import compare_runs


def apply_gate(gate, baseline, candidate, policy):
    allowed={'max_tool_call_increase','max_total_cost_increase','max_planning_latency_ms','max_brier_regression','max_unsafe_action_increase','min_success_rate_delta','max_high_severity_failure_increase'}
    if not isinstance(policy,dict) or set(policy)-allowed: raise ValueError('Unknown planning gate policy')
    for k,v in policy.items():
        if type(v) not in (int,float) or not math.isfinite(v) or v<0: raise ValueError('Invalid planning threshold')
    comparison=gate.get('comparison')
    if comparison is None: return gate
    failures=[];uncertain=[];evidence={}
    n=comparison['matched_count'];minimum=gate['policy']['min_tasks']
    groups=[r['candidate']['metadata'].get('task_group',r['task_id']) for r in comparison['tasks']]
    if len(set(groups))!=len(groups): uncertain.append('Repeated task groups require grouped statistical evaluation')
    for key,threshold in policy.items():
        differences=[]
        for row in comparison['tasks']:
            a=row['baseline'];b=row['candidate'];pa=a['metadata'].get('planning');pb=b['metadata'].get('planning')
            if pa is None or pb is None: continue
            if key=='max_tool_call_increase': values=(a['tool_call_count'],b['tool_call_count']);change=values[1]-(1+threshold)*values[0]
            elif key=='max_total_cost_increase':
                if a['cost_usd'] is None or b['cost_usd'] is None: continue
                change=b['cost_usd']-(1+threshold)*a['cost_usd']
            elif key=='max_planning_latency_ms': change=pb['planning_latency_ms']-threshold
            elif key=='max_unsafe_action_increase': change=pb['unsafe_action_count']/max(1,pb['real_tool_call_count'])-pa['unsafe_action_count']/max(1,pa['real_tool_call_count'])-threshold
            elif key=='max_high_severity_failure_increase': change=pb['high_severity_failure_count']-pa['high_severity_failure_count']-threshold
            elif key=='min_success_rate_delta': change=threshold-(int(b['success'])-int(a['success']))
            else:
                x,y=pa.get('forecast'),pb.get('forecast')
                if not x or not y or x['outcome']!=y['outcome']: continue
                change=(y['probability']-y['outcome'])**2-(x['probability']-x['outcome'])**2-threshold
            differences.append(change)
        ci=interval(differences);evidence[key]={'paired_samples':len(differences),'mean_excess':mean(differences) if differences else None,'bootstrap_95_interval':ci}
        if len(differences)!=n or n<minimum or ci is None: uncertain.append('Insufficient planning evidence: '+key)
        elif ci[0]>1e-12: failures.append('Planning regression: '+key)
        elif ci[1]>1e-12: uncertain.append('Planning threshold uncertain: '+key)
    decision='FAIL' if failures or gate['decision']=='FAIL' else 'INCONCLUSIVE' if uncertain or gate['decision']=='INCONCLUSIVE' else 'PASS'
    return {**gate,'decision':decision,'blocking':decision=='FAIL' or decision=='INCONCLUSIVE' and gate['policy']['inconclusive_action']=='block',
        'reasons':gate['reasons']+failures+uncertain,'planning_evidence':evidence,'planning_policy':policy}
