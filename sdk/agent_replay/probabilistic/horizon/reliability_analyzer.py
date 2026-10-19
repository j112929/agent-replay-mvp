"""Aligned real steps only. Counterfactual action mismatches stay simulated-only."""
import asyncio
from collections import defaultdict
from dataclasses import asdict
import math
from pathlib import Path
from statistics import mean
from ...planning.contracts import AgentContext, CandidateAction, PredictionConfig
from ...serialization import digest
from ...storage import read, within
from ...benchmark.store import RunStore
from ..calibration.metrics import evaluate_metrics
from .prediction_error import state_error, empirical_crps
from ..experiments.statistics import bootstrap


def _key(action):
    return digest([action['tool_name'],action['action_payload']])


def _summarize(rows):
    fields=sorted({f for r in rows for f in r['numeric_errors']})
    return {'sample_count':len(rows),'independent_groups':len({r['task_group'] for r in rows}),
        'numeric_state':{field:{'mae':mean(r['numeric_errors'][field]['absolute_error'] for r in rows if field in r['numeric_errors']),
            'rmse':math.sqrt(mean(r['numeric_errors'][field]['squared_error'] for r in rows if field in r['numeric_errors'])),
            'mae_95_interval':bootstrap([r for r in rows if field in r['numeric_errors']],lambda rr:mean(r['numeric_errors'][field]['absolute_error'] for r in rr)),
            'rmse_95_interval':bootstrap([r for r in rows if field in r['numeric_errors']],lambda rr:math.sqrt(mean(r['numeric_errors'][field]['squared_error'] for r in rr)))} for field in fields},
        'health_crps':mean(r['health_crps'] for r in rows if r.get('health_crps') is not None) if any(r.get('health_crps') is not None for r in rows) else None,
        'calibration':evaluate_metrics([{'probability':r['success_by_horizon_probability'],'outcome':r['success_by_horizon_outcome'],
            'task_group':r['task_group']} for r in rows if r.get('success_by_horizon_probability') is not None])}


def analyze(run_directory, model, horizons=(1,2,4,8,16,32), strategy='open_loop'):
    directory=Path(run_directory)
    run,results=RunStore(directory).load()
    records=[];skipped=0;failures=[];replan_diagnostics=[];prediction_calls=0
    for result in results:
        trace=read(within(directory,result.trajectory_path))
        real=[s for s in trace['steps'] if s['kind']=='tool']
        sessions=trace.get('metadata',{}).get('planning',{}).get('sessions',[])
        group=result.metadata['task_group']
        material=[]
        # Teacher forcing always uses the actual prior state, never imagined history.
        for index,step in enumerate(real):
            inp=step['input']['value'];actual=step['output']['value'];before=inp['state'];after={**before,**actual['state_delta']}
            context=AgentContext(result.task_id,group,'Complete the synthetic reliability workflow',before,index,
                inp['environment_version'],[],trace['id'],run['id'])
            prediction=asyncio.run(model.predict(context,CandidateAction(**inp['action']),PredictionConfig(seed=index)))
            prediction_calls+=1
            predicted={f:sum(o['probability']*({**before,**o['state_delta']})[f] for o in prediction.outcomes) for f in ('health','progress')}
            errors=state_error(predicted,after)
            health=[({**before,**o['state_delta']})['health'] for o in prediction.outcomes]
            # Exact finite-distribution CRPS, with probabilities (not equal outcomes).
            crps=sum(o['probability']*abs(h-after['health']) for o,h in zip(prediction.outcomes,health))
            crps-=.5*sum(a['probability']*b['probability']*abs(x-y) for a,x in zip(prediction.outcomes,health) for b,y in zip(prediction.outcomes,health))
            probability=sum(o['probability'] for o in prediction.outcomes if o.get('success') is True and o['terminal'])
            records.append({'protocol':'teacher_forced_one_step','horizon':1,'run_id':run['id'],'task_id':result.task_id,'task_group':group,
                'trajectory_id':trace['id'],'step_id':step['id'],'numeric_errors':errors,'health_crps':crps,
                'success_by_horizon_probability':probability,'success_by_horizon_outcome':bool(after.get('terminal') and after.get('success')),
                'label_scope':'success_within_one_next_step'})
            if errors.get('health',{}).get('absolute_error',0)>.75:material.append(index+1)
        for index,session in enumerate(sessions):
            reference=session.get('research',{}).get('sidecar_path')
            if not reference:continue
            sidecar=read(within(directory,reference),limit=16*1024*1024)
            if digest(sidecar)!=session['research']['sidecar_digest'] or sidecar['trajectory_id']!=trace['id'] or sidecar.get('simulated_only') is not True:
                raise ValueError('Research sidecar integrity/provenance mismatch')
            selected=sidecar['selected_candidate_id'];rollouts=[r for r in sidecar['rollouts'] if r['candidate_id']==selected]
            origin=session['origin']['state_version']
            for horizon in horizons:
                if horizon>sidecar['sampling_config']['horizon']:continue
                actual_steps=real[origin:origin+horizon]
                if not actual_steps:continue
                after=dict(sidecar['origin_state'])
                for step in actual_steps:after.update(step['output']['value']['state_delta'])
                if len(actual_steps)<horizon and not after['terminal']:continue
                aligned=[]
                for rollout in rollouts:
                    simulated=rollout['steps'][:horizon]
                    if len(simulated)<horizon and not rollout['terminal']:continue
                    if any(_key(a['action'])!=_key(b['input']['value']['action']) for a,b in zip(simulated,actual_steps)):continue
                    state=dict(sidecar['origin_state'])
                    for step in simulated:state.update(step['state_delta'])
                    aligned.append(state)
                if len(aligned)!=len(rollouts):
                    # Filtering incompatible outcomes would bias calibration; skip whole set.
                    skipped+=1;continue
                predicted={f:mean(s[f] for s in aligned) for f in ('health','progress')}
                records.append({'protocol':'free_running_open_loop' if strategy=='open_loop' else 'closed_loop_observed_execution',
                    'horizon':horizon,'run_id':run['id'],'task_id':result.task_id,'task_group':group,'trajectory_id':trace['id'],
                    'planning_id':session['session_id'],'step_id':actual_steps[-1]['id'],'numeric_errors':state_error(predicted,after),
                    'health_crps':empirical_crps([s['health'] for s in aligned],after['health']),
                    'success_by_horizon_probability':mean(bool(s['terminal'] and s['success']) for s in aligned),
                    'success_by_horizon_outcome':bool(after['terminal'] and after['success']),
                    'label_scope':'success_within_specified_horizon','absorbing_terminal':True,'sampled_futures':len(aligned)})
        events=result.metadata['probabilistic']['replanning_events']
        triggered={s['origin']['state_version'] for s in sessions if s.get('research',{}).get('replanning_event',{} ) and s['research']['replanning_event']['reason']!='initial_or_exhausted_plan'}
        replan_diagnostics.append({'task_id':result.task_id,'task_group':group,'first_material_error_step':min(material) if material else None,
            'material_error_count':len(material),'replanning_events':len(triggered),
            'false_positive_triggers':len(triggered-set(material)),'missed_material_error_triggers':len(set(material)-triggered),
            'threshold':.75,'threshold_status':'synthetic_screening_diagnostic_not_validated'})
        if not result.success:
            categories=['tool_environment_nondeterminism']
            if len(material)>1:categories.append('compounding_prediction_error')
            if result.metadata['probabilistic']['distribution']=='shift':categories.append('distribution_shift')
            if len(triggered)>len(real)//2:categories.append('replanning_instability')
            failures.append({'task_id':result.task_id,'trajectory_id':trace['id'],'trajectory_path':result.trajectory_path,
                'categories':categories,'support':replan_diagnostics[-1],'causal_status':'diagnostic_hypotheses_not_verified_causes'})
    grouped=defaultdict(list)
    for row in records:grouped[(row['protocol'],row['horizon'])].append(row)
    return {'schema_version':'horizon-evaluation.v1','run_id':run['id'],'strategy':strategy,'records':records,
        'by_protocol':{protocol:{str(h):_summarize(rr) for (p,h),rr in grouped.items() if p==protocol} for protocol,h in grouped},
        'simulated_only_or_action_mismatched_windows':skipped,'replanning_diagnostics':replan_diagnostics,'failures':failures,
        'offline_evaluation_prediction_calls':prediction_calls,
        'limitations':['Teacher forced, free running and closed loop are separate protocols.',
            'No counterfactual future is labeled when executed actions differ. Entire incompatible sample sets are excluded.',
            'Terminal states are absorbing for horizon evaluation; success means completion within that horizon.',
            'Replan thresholds are explicit synthetic screening settings, not production-validated detectors.']}
