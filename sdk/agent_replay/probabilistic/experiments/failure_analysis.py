"""Evidence-backed diagnostic hypotheses, not automated causal attribution."""
from ...storage import read, within


def analyze_failures(directory,results):
    rows=[]
    for r in results:
        if r.success:continue
        trace=read(within(directory,r.trajectory_path));sessions=trace.get('metadata',{}).get('planning',{}).get('sessions',[])
        evidence=r.metadata.get('probabilistic',{});planning=r.metadata.get('planning',{})
        categories=[];support={}
        first=sessions[0] if sessions else {}
        selected=first.get('selection',{}).get('selected_candidate_id')
        selected_scores=[m for m in first.get('research',{}).get('candidate_evaluations',[]) if m['candidate_id']==selected]
        if evidence.get('severe_failure'):
            categories.append('tool_environment_nondeterminism')
            if selected_scores and selected_scores[0]['severe_failure_probability']==0:
                categories.extend(('model_exploitation_by_planner','incorrect_uncertainty_estimates'))
                support['unobserved_predicted_tail']={'samples':selected_scores[0]['sampled_rollouts'],
                    'predicted_severe_rate':0,'actual_severe_failure':True}
        if evidence.get('distribution')=='shift':categories.append('distribution_shift')
        if any(s['status']=='failed' for s in sessions):categories.append('invalid_predicted_observations')
        if planning.get('prediction_errors',0)>1:categories.append('compounding_prediction_error')
        replans=len(evidence.get('replanning_events',[]))
        if replans>r.tool_call_count/2 and r.tool_call_count>2:categories.append('replanning_instability')
        if r.duration_ms and planning.get('planning_latency_ms',0)>.8*r.duration_ms:categories.append('excessive_planning_cost')
        support.update(planning_latency_ms=planning.get('planning_latency_ms'),duration_ms=r.duration_ms,
            prediction_error_count=planning.get('prediction_errors'),replan_count=replans)
        rows.append({'task_id':r.task_id,'trajectory_id':r.trajectory_id,'trajectory_path':r.trajectory_path,
            'categories':sorted(set(categories)),'supporting_metrics':support,
            'replay':{'mode':'MOCK_REPLAY','external_operations':0,'execution':'recorded_only'},
            'interpretation':'Diagnostic hypotheses; one rare failure does not establish model miscalibration or planner exploitation.'})
    return rows
