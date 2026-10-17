"""Probability metrics require explicit binary outcomes and independent task groups."""
import math
import random
from statistics import mean


def interval(values, seed=0):
    if len(values)<2: return None
    rng=random.Random(seed);n=len(values)
    means=sorted(mean(rng.choices(values,k=n)) for _ in range(400))
    return [means[9],means[389]]


def calibration(rows, bins=10):
    if type(bins) is not int or not 1<=bins<=50: raise ValueError('Invalid calibration bins')
    pairs=[]
    for row in rows:
        p=row.get('probability');y=row.get('outcome')
        if type(p) not in (int,float) or not math.isfinite(p) or not 0<=p<=1 or type(y) is not bool: raise ValueError('Explicit binary label and probability required')
        pairs.append((p,int(y)))
    if not pairs: return {'count':0,'brier_score':None,'ece':None,'roc_auc':None,'pr_auc':None,'reliability':[],'risk_coverage':[]}
    reliability=[];ece=0
    for i in range(bins):
        group=[(p,y) for p,y in pairs if min(bins-1,int(p*bins))==i]
        if not group: continue
        confidence=mean(p for p,y in group);observed=mean(y for p,y in group)
        ece+=len(group)/len(pairs)*abs(confidence-observed)
        reliability.append({'bin':i,'count':len(group),'predicted':confidence,'observed':observed})
    positive=[p for p,y in pairs if y];negative=[p for p,y in pairs if not y]
    auc=mean(float(a>b)+.5*float(a==b) for a in positive for b in negative) if positive and negative else None
    average_precision=None
    if positive and negative:
        tp=0;seen=0;average_precision=0
        for threshold in sorted(set(p for p,y in pairs),reverse=True):
            group=[y for p,y in pairs if p==threshold];new=sum(group)
            tp+=new;seen+=len(group);average_precision+=new/len(positive)*tp/seen
    ordered=sorted(pairs,key=lambda pair:abs(pair[0]-.5),reverse=True)
    risk=[]
    for n in range(1,len(ordered)+1):
        risk.append({'coverage':n/len(ordered),'classification_error':mean((p>=.5)!=bool(y) for p,y in ordered[:n])})
    errors=[(p-y)**2 for p,y in pairs]
    return {'count':len(pairs),'positive_count':len(positive),'negative_count':len(negative),
        'brier_score':mean(errors),'brier_95_interval':interval(errors),'ece':ece,
        'roc_auc':auc,'pr_auc':average_precision,'pr_auc_method':'stepwise_average_precision',
        'reliability':reliability,'risk_coverage':risk,
        'limitations':'Descriptive probability evaluation; bootstrap interval assumes independent supplied task groups. Confidence is not certification.'}


def trace_metrics(trace):
    sessions=trace.get('metadata',{}).get('planning',{}).get('sessions',[])
    budgets=[s['budget'] for s in sessions]
    cost=[b.get('cost_usd') for b in budgets]
    # One final task-success forecast per trajectory, not H×N imagined labels.
    eligible=[s for s in sessions if s.get('selection',{}).get('success_probability') is not None]
    last=eligible[-1] if eligible else None
    label=last.get('task_outcome',{}) if last else {}
    correction=trace.get('metadata',{}).get('planning',{}).get('correction_summary',{})
    forecast={'probability':last['selection']['success_probability'],'outcome':label['success']} if last and label.get('observed') else None
    return {'enabled':bool(sessions),'session_count':len(sessions),
        'planning_latency_ms':sum(s.get('planning_latency_ms',0) for s in sessions),
        'planning_tokens':sum(b.get('tokens',0) for b in budgets) if all(b.get('usage_complete') for b in budgets) else None,
        'planning_cost_usd':sum(cost) if all(c is not None for c in cost) else None,
        'prediction_calls':sum(b.get('predictions',0) for b in budgets),
        'model_calls':sum(b.get('model_calls',0) for b in budgets),
        'recovery_attempts':correction.get('replans',0),'recovered':correction.get('recovered',False),
        'unsafe_action_count':sum(s.get('output',{}).get('value',{}).get('unsafe') is True for s in trace['steps'] if s['kind']=='tool' and isinstance(s.get('output',{}).get('value'),dict)),
        'blocked_action_count':sum(s['status']=='execution_denied' for s in sessions),
        'high_severity_failure_count':sum(s.get('output',{}).get('value',{}).get('severity')=='high' for s in trace['steps'] if s['kind']=='tool' and isinstance(s.get('output',{}).get('value'),dict)),
        'prediction_errors':sum(not o['outcome_match'] for s in sessions for o in s.get('outcomes',[])),
        'forecast':forecast,'real_tool_call_count':sum(s['kind']=='tool' for s in trace['steps']),
        'real_retry_count':sum(max(0,s.get('attempt',1)-1) for s in trace['steps'] if s['kind']=='tool')}


def aggregate_planning(results):
    values=[r.metadata.get('planning') for r in results]
    present=[p for p in values if p is not None]
    if not present: return None
    from ..benchmark.metrics import distribution
    forecasts=[p['forecast'] for p in present if p.get('forecast')]
    return {'task_count':len(present),'missing':len(results)-len(present),
        'recovery_success_rate':sum(p.get('recovered',False) for p in present)/sum(p.get('recovery_attempts',0)>0 for p in present) if any(p.get('recovery_attempts',0)>0 for p in present) else None,
        'distributions':{key:distribution([p.get(key) for p in values if p is not None]) for key in
            ('planning_latency_ms','planning_tokens','planning_cost_usd','real_tool_call_count','real_retry_count')},
        'unsafe_action_rate':sum(p['unsafe_action_count'] for p in present)/max(1,sum(p['real_tool_call_count'] for p in present)),
        'prediction_errors':sum(p['prediction_errors'] for p in present),
        'calibration':calibration(forecasts)}
