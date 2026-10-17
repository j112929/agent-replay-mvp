"""Allowlisted aggregate public artifact: never traces, fixtures, histories or datasets."""
import math
from ..planning.contracts import data


def numbers(value,keys):
    return {k:(value.get(k) if type(value.get(k)) in (int,float) and math.isfinite(value[k]) else None) for k in keys}


def distribution(value):return numbers(value,('count','missing','mean','median','min','max','p90','p95'))


def calibration(value):
    result=numbers(value,('count','positive_count','negative_count','brier_score','ece','roc_auc','pr_auc'))
    for key,fields in [('reliability',('bin','count','predicted','observed')),('risk_coverage',('coverage','classification_error'))]:
        result[key]=[numbers(row,fields) for row in value.get(key,[])[:100] if isinstance(row,dict)]
    return result


def summary(value):
    result=numbers(value,('count','schema_validity','exact_match','field_accuracy','terminal_accuracy','state_transition_validity',
        'negative_log_likelihood','latency_ms','tokens_per_prediction','cost_per_prediction','prediction_failure_rate'))
    result['calibration']=calibration(value.get('calibration',{}))
    codes={'unsupported_prediction','schema_violation','environment_mismatch','missing_context','incorrect_tool_output','incorrect_state_transition','incorrect_terminal_prediction','prediction_timeout'}
    result['error_distribution']={k:v for k,v in value.get('error_distribution',{}).items() if k in codes and type(v) is int and v>=0}
    return result


def public_report(experiment):
    if experiment.get('schema_version')!='world-model-experiment.v1':raise ValueError('Unsupported experiment')
    if experiment.get('synthetic') is not True:raise ValueError('Public demo export requires explicitly synthetic experiment evidence')
    modes={}
    allowed={'baseline','safe-policy','shadow','active','correcting','horizon1','horizon5','no-risk','no-uncertainty'}
    for name,s in experiment['modes'].items():
        if name not in allowed:continue
        planning=s.get('planning') or {}
        modes[name]={**numbers(s,('task_count','success_rate','pass_count','fail_count')),
            'distributions':{k:distribution(s['distributions'][k]) for k in ('duration_ms','cost_usd','tool_call_count')},
            'planning':{**numbers(planning,('recovery_success_rate','unsafe_action_rate','prediction_errors')),
                'calibration':calibration(planning.get('calibration',{}))}}
    decision=experiment.get('quality_gate',{}).get('decision')
    return data({'schema_version':'world-model-public.v1','version':'0.5.0','task_count':experiment['task_count'],
        'modes':modes,'evaluations':{name:{'model_id':value['model_id'],'model_version':value['model_version'],'summary':summary(value['summary'])} for name,value in experiment['evaluations'].items() if name in ('oracle','trained')},
        'quality_gate':{'decision':decision if decision in ('PASS','FAIL','INCONCLUSIVE') else None,'reasons':['Consult the private CI report for complete gate evidence.']},
        'limitations':['Synthetic Refund doubles only; no production reliability claim','Compare safe-policy without a world model to isolate rule changes from planning','Tiny CPU LoRA tests mechanics, not useful prediction quality; GPU and live providers remain unvalidated'],'synthetic':True})
