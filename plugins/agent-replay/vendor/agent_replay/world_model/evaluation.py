"""Independent offline structured prediction evaluation and linked error artifacts."""
import asyncio
from dataclasses import asdict
from datetime import datetime,timezone
import math
from pathlib import Path
from statistics import mean
from collections import defaultdict,Counter
from .interfaces import UnsupportedPrediction
import time
from ..planning.contracts import AgentContext,CandidateAction,PredictionConfig,PredictionDistribution
from ..planning.metrics import calibration
from ..serialization import canonical,digest
from ..storage import save


def fields(expected, actual, prefix=''):
    result={}
    for key in sorted(set(expected)|set(actual)):
        path=prefix+'/'+key;a=expected.get(key);b=actual.get(key)
        if isinstance(a,dict) and isinstance(b,dict):result.update(fields(a,b,path))
        else:result[path]={'match':key in expected and key in actual and canonical(a)==canonical(b),'predicted':a,'actual':b}
    return result


async def evaluate(model, rows, output=None):
    results=[];forecasts=[];numeric=defaultdict(list);latencies=[];tokens=[];costs=[]
    for row in rows:
        started=time.perf_counter();record={'prediction_id':digest([model.version,row['source_trajectory_id'],row['source_step_id']]),'trajectory_id':row['source_trajectory_id'],'step_id':row['source_step_id'],'task_group':row['task_group'],
            'model_version':model.version,'environment_version':row['environment_version'],'timestamp':datetime.now(timezone.utc).isoformat(),
            'error_type':None,'schema_valid':False,'actual_observation':row['actual_observation']}
        try:
            c=AgentContext(row.get('task_id',row['source_trajectory_id']),row['task_group'],'Predict the next environment observation',row['history_context'],row.get('step_index',0),row['environment_version'],row.get('history',[]))
            a=CandidateAction(row['source_step_id'],'tool',row['action']['action_payload'],row['action']['tool_name'])
            prediction=await asyncio.wait_for(model.predict(c,a,PredictionConfig()),10)
            prediction=PredictionDistribution.parse(asdict(prediction),a.candidate_id)
            chosen=max(prediction.outcomes,key=lambda o:o.get('probability') if o.get('probability') is not None else -1)
            diff=fields(chosen['observation'],row['actual_observation']);exact=all(f['match'] for f in diff.values())
            terminal_match=chosen['terminal']==row['actual_observation']['terminal'] if type(row['actual_observation'].get('terminal')) is bool else None
            record.update(prediction_id=prediction.prediction_id,schema_valid=True,predicted_observation=chosen['observation'],
                field_comparison=diff,exact_match=exact,terminal_match=terminal_match,
                state_transition_valid=all(chosen['observation']['state_delta'].get(k)==v for k,v in chosen['state_delta'].items()) if isinstance(chosen['observation'].get('state_delta'),dict) else None,
                confidence=chosen.get('probability'),calibration_status=prediction.calibration_status,
                error_type=None if exact else 'incorrect_terminal_prediction' if terminal_match is False else 'incorrect_state_transition' if chosen['state_delta']!=row['actual_state_delta'] else 'incorrect_tool_output')
            for path,value in diff.items():
                if type(value['predicted']) in (int,float) and type(value['actual']) in (int,float):numeric[path].append(value['predicted']-value['actual'])
            label=row['actual_observation'].get('success')
            if row['actual_observation'].get('terminal') and type(label) is bool and all(o.get('probability') is not None and o.get('success_probability') is not None for o in prediction.outcomes):
                p=sum(o['probability']*o['success_probability'] for o in prediction.outcomes)
                forecasts.append({'probability':p,'outcome':label})
                record['calibration_bucket']=min(9,int(p*10))
            tokens.append(prediction.prediction_token_usage);costs.append(prediction.cost_usd)
        except (ValueError,KeyError,TypeError,asyncio.TimeoutError) as exc:
            code=exc.code if isinstance(exc,UnsupportedPrediction) else 'prediction_timeout' if isinstance(exc,asyncio.TimeoutError) else 'schema_violation'
            record.update(error_type=code,exact_match=False,terminal_match=False)
            tokens.append(None);costs.append(None)
        latency=(time.perf_counter()-started)*1000;latencies.append(latency);record['latency_ms']=latency
        record['error_score']=0 if record['exact_match'] else 1;results.append(record)
    count=len(results);valid=[r for r in results if r['schema_valid']]
    probabilities=calibration(forecasts)
    nll=mean(-math.log(max(1e-12,r['probability'] if r['outcome'] else 1-r['probability'])) for r in forecasts) if forecasts else None
    field_values=[f['match'] for r in valid for f in r['field_comparison'].values()]
    summary={'count':count,'schema_validity':len(valid)/count if count else None,
        'exact_match':mean(r['exact_match'] for r in results) if count else None,
        'field_accuracy':mean(field_values) if field_values else None,
        'terminal_accuracy':mean([r['terminal_match'] for r in valid if r['terminal_match'] is not None]) if any(r['terminal_match'] is not None for r in valid) else None,
        'state_transition_validity':mean([r['state_transition_valid'] for r in valid if r['state_transition_valid'] is not None]) if any(r['state_transition_valid'] is not None for r in valid) else None,
        'state_transition_metric':'Internal observation/state-delta consistency; actual causal accuracy is measured separately by field/exact match',
        'continuous_errors':{path:{'mae':mean(abs(x) for x in values),'rmse':math.sqrt(mean(x*x for x in values)),'count':len(values)} for path,values in numeric.items()},
        'error_distribution':dict(Counter(r['error_type'] for r in results if r['error_type'])),
        'negative_log_likelihood':nll,'latency_ms':mean(latencies) if latencies else None,
        'tokens_per_prediction':mean(tokens) if tokens and all(x is not None for x in tokens) else None,
        'cost_per_prediction':mean(costs) if costs and all(x is not None for x in costs) else None,
        'prediction_failure_rate':1-len(valid)/count if count else None,'calibration':probabilities,
        'numeric_scope':'MAE/RMSE separately per structured numeric field, never mixed units or text similarity'}
    value={'schema_version':'world-evaluation.v1','model_id':model.model_id,'model_version':model.version,
        'dataset_digest':digest(rows),'task_groups':sorted({r['task_group'] for r in rows}),
        'source_trajectories':sorted({r['source_trajectory_id'] for r in rows}),'summary':summary,'errors':results}
    if output:save(output,value)
    return value
