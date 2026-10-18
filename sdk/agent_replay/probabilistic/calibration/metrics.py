"""Binary diagnostics extend the existing probability metrics and task grouping."""
import math
from statistics import mean
from .schemas import probability, CalibrationResult
from .calibrators import logistic_fit, CLIP
from ...planning.metrics import calibration
from ..experiments.statistics import bootstrap


def evaluate_metrics(rows, bins=10, seed=0):
    if type(bins) is not int or not 1 <= bins <= 50:
        raise ValueError('Invalid calibration bin count')
    for row in rows:
        probability(row['probability'])
        if type(row.get('outcome')) is not bool:
            raise ValueError('Verified binary outcome required')
    # Reuse established Brier, fixed-bin ECE, tie-correct AUC and AP definitions.
    base = calibration(rows, bins)
    def loss(rr):
        return mean(-math.log(min(1-CLIP,max(CLIP,r['probability'])) if r['outcome'] else 1-min(1-CLIP,max(CLIP,r['probability']))) for r in rr)
    adaptive = []
    ordered = sorted(rows, key=lambda r:r['probability'])
    # Equal-frequency bins never split identical confidence values.
    for row in ordered:
        if not adaptive or len(adaptive[-1]) >= max(1, math.ceil(len(rows)/bins)) and adaptive[-1][-1]['probability'] != row['probability']:
            adaptive.append([])
        adaptive[-1].append(row)
    aece = sum(len(g)/len(rows)*abs(mean(r['probability'] for r in g)-mean(r['outcome'] for r in g)) for g in adaptive) if rows else None
    slope = logistic_fit(rows, ridge=0) if rows else None
    cis = {name: bootstrap(rows, stat, seed) for name,stat in {
        'brier_score': lambda rr: mean((r['probability']-r['outcome'])**2 for r in rr),
        'log_loss': loss,
        'ece': lambda rr: _ece(rr, bins)}.items()} if rows else {}
    result = CalibrationResult(base['brier_score'], loss(rows) if rows else None, base['ece'], len(rows), base['reliability'], cis)
    from dataclasses import asdict
    # Selectively abstain on low success probability, with all equal-score ties.
    selective = []
    for threshold in sorted({r['probability'] for r in rows}, reverse=True):
        selected = [r for r in rows if r['probability'] >= threshold]
        selective.append({'threshold': threshold, 'coverage': len(selected)/len(rows), 'failure_risk': 1-mean(r['outcome'] for r in selected)})
    return {**asdict(result), 'independent_groups': len({r['task_group'] for r in rows}), 'adaptive_ece': aece,
            'positive_count':sum(r['outcome'] for r in rows),'negative_count':sum(not r['outcome'] for r in rows),
            'log_loss_clipping': CLIP, 'calibration_slope': slope['slope'] if slope else None,
            'calibration_intercept': slope['intercept'] if slope else None,
            'slope_status': 'estimated_unpenalized' if slope else 'unavailable_single_class_constant_or_separation',
            'roc_auc': base['roc_auc'], 'average_precision': base['pr_auc'], 'risk_coverage': selective,
            'limitations': ['ECE depends on binning and sample size; not a sufficient calibration score.',
                           'Scenario-cluster bootstrap assumes independent scenario groups; intervals may be unstable for small samples.',
                           'Slope/intercept unavailable under separation, constant forecasts or a single outcome class.']}


def _ece(rows, bins):
    # Avoid AUC and repeated bootstrap computation inside bootstrap iterations.
    groups = [[] for _ in range(bins)]
    for r in rows:
        groups[min(bins-1,int(r['probability']*bins))].append(r)
    return sum(len(g)/len(rows)*abs(mean(r['probability'] for r in g)-mean(r['outcome'] for r in g)) for g in groups if g)
