"""Structured prediction errors, never textual similarity as a success label."""
from ..serialization import canonical


def evaluate_prediction(prediction, actual, step_id=None):
    matches=[]
    for outcome in prediction.outcomes:
        expected=outcome['observation']
        fields={k:canonical(expected.get(k))==canonical(actual.get(k)) for k in
                ('status','error_category','state_delta','changed_resources','side_effects') if k in expected or k in actual}
        if actual.get('terminal') and type(actual.get('success')) is bool and outcome.get('success') is not None:
            fields['task_success']=outcome['success']==actual['success']
        errors={key:actual[key]-expected[key] for key in ('cost_usd','remaining_steps')
                if type(actual.get(key)) in (int,float) and type(expected.get(key)) in (int,float)}
        matches.append({'outcome_match':bool(fields) and all(fields.values()),'fields':fields,'probability':outcome.get('probability'),
            'numeric_errors':errors})
    return {'prediction_id':prediction.prediction_id,'candidate_id':prediction.candidate_id,
        'actual_observation_ref':{'step_id':step_id},'evaluator_version':'structured-exact.v1',
        'outcome_match':any(m['outcome_match'] for m in matches),'outcome_comparisons':matches,
        'prediction_error_types':sorted({k for m in matches for k,v in m['fields'].items() if not v}),
        'binary_task_label':actual.get('success') if actual.get('terminal') and type(actual.get('success')) is bool else None,
        'calibration_status':prediction.calibration_status}
