"""One split manifest, no hyperparameter selection on the held-out test set."""
from .calibrators import fit, transform
from .metrics import evaluate_metrics
from ..experiments.statistics import paired_effect, holm


def evaluate_calibration(rows, seed=0, min_groups=20):
    methods = sorted({r['source'] for r in rows})
    raw = [r for r in rows if r['source'] == 'raw_agent']
    train = [r for r in raw if r['split'] == 'train']
    heldout = [r for r in raw if r['split'] != 'train']
    partitions = {}
    families = {}
    for row in rows:
        key = row['task_group']
        if key in partitions and partitions[key] != row['split']:
            raise ValueError('Task group crosses split boundary')
        partitions[key] = row['split']
        family=row.get('task_family')
        if family:
            if family in families and families[family]!=row['split']:
                raise ValueError('Task family crosses declared held-out split')
            families[family]=row['split']
    models, failures = {}, {}
    for method in ('none', 'platt', 'isotonic'):
        try:
            models[method] = fit(train, method, min_groups)
        except ValueError as exc:
            failures[method] = str(exc)
    reports, comparisons = {}, []
    for split in ('validation', 'test', 'shift'):
        original = [r for r in heldout if r['split'] == split]
        evaluated = {source: [r for r in rows if r['source'] == source and r['split'] == split] for source in methods}
        for method, model in models.items():
            if method != 'none':
                evaluated[method] = transform(model, original)
        reports[split] = {method: evaluate_metrics(values, seed=seed) for method, values in evaluated.items()}
        for method, values in evaluated.items():
            if method == 'raw_agent' or not original or not values:
                continue
            a = [{**r, 'brier': (r['probability']-r['outcome'])**2} for r in original]
            b = [{**r, 'brier': (r['probability']-r['outcome'])**2} for r in values]
            effect = paired_effect(a, b, 'brier', seed, min_groups)
            comparisons.append({'split': split, 'method': method, **effect})
    for item, adjusted in zip(comparisons, holm([r['p_value'] for r in comparisons])):
        item['holm_p_value'] = adjusted
        if adjusted > .05:
            item['decision'] = 'INCONCLUSIVE'
    return {'schema_version': 'calibration-evaluation.v1', 'reports': reports, 'comparisons': comparisons,
            'calibrators': models, 'unavailable': failures,
            'selection_protocol': 'Fixed Platt/isotonic methods fit train only; validation/test/shift reported separately; no best-test selection.'}
