"""Offline histogram calibration and explicit held-out promotion, no model weights."""
from collections import defaultdict
from statistics import mean
from .metrics import calibration
from .dataset import assert_disjoint
from ..serialization import digest
from ..storage import save


def fit(rows, bins=5, min_samples=20):
    calibration(rows,bins)
    if len(rows)<min_samples or len({r['outcome'] for r in rows})<2: raise ValueError('Insufficient calibration examples/classes')
    groups=[r.get('task_group') for r in rows]
    if any(not isinstance(g,str) for g in groups) or len(set(groups))!=len(groups): raise ValueError('Use one independent calibration observation per task group')
    counts=defaultdict(list)
    for row in rows: counts[min(bins-1,int(row['probability']*bins))].append(row['outcome'])
    values={str(key):(sum(labels)+1)/(len(labels)+2) for key,labels in counts.items()}
    return {'schema_version':'probability-calibrator.v1','method':'laplace_histogram','bins':bins,'values':values,
        'training_groups':groups,'training_digest':digest(rows),'status':'fitted_unvalidated'}


def transform(model, probability):
    return model['values'].get(str(min(model['bins']-1,int(probability*model['bins']))),probability)


def compare_versions(baseline, candidate, min_groups=20):
    if {r['task_group'] for r in baseline}!={r['task_group'] for r in candidate}: raise ValueError('Paired prediction groups required')
    if len(baseline)!=len({r['task_group'] for r in baseline}) or len(candidate)!=len({r['task_group'] for r in candidate}): raise ValueError('Repeated task groups need aggregation')
    aa={r['task_group']:r for r in baseline};bb={r['task_group']:r for r in candidate}
    if any(aa[g]['outcome']!=bb[g]['outcome'] for g in aa): raise ValueError('Prediction labels differ')
    before=calibration(baseline);after=calibration(candidate)
    from .metrics import interval
    differences=[(bb[g]['probability']-bb[g]['outcome'])**2-(aa[g]['probability']-aa[g]['outcome'])**2 for g in aa]
    ci=interval(differences)
    decision='INCONCLUSIVE' if len(aa)<min_groups or ci is None or ci[0]<=0<=ci[1] else 'IMPROVED' if ci[1]<0 else 'REGRESSED'
    return {'baseline':before,'candidate':after,'brier_delta':after['brier_score']-before['brier_score'] if aa else None,
        'paired_delta_95_interval':ci,'decision':decision,'groups':len(aa)}


def promote(model, training, validation, test, baseline_rows, candidate_rows, path):
    assert_disjoint(training,validation,test)
    groups={r['task_group'] for r in test}
    if {r['task_group'] for r in baseline_rows}!=groups or set(model['training_groups'])&groups: raise ValueError('Promotion evaluation is not held out')
    if not set(model['training_groups'])<={r['task_group'] for r in training}: raise ValueError('Calibrator training provenance mismatch')
    before={r['task_group']:r for r in baseline_rows}
    if any(r['task_group'] not in before or abs(r['probability']-transform(model,before[r['task_group']]['probability']))>1e-12 for r in candidate_rows): raise ValueError('Candidate forecasts do not match fitted calibrator')
    result=compare_versions(baseline_rows,candidate_rows)
    if result['decision']!='IMPROVED': raise ValueError('Model promotion requires held-out predictive improvement')
    record={'schema_version':'world-model-promotion.v1','model':model,'evaluation':result,
        'training_digest':digest(training),'validation_digest':digest(validation),'test_digest':digest(test),
        'deployment':'offline_only','online_weight_updates':False}
    save(path,record);return record
