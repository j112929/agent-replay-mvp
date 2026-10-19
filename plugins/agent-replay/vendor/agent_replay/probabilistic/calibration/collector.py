"""Strict many-method forecasts to one verified outcome join, with missingness."""
from dataclasses import asdict
from .schemas import SuccessForecast, VerifiedOutcome, timestamp


def join_outcomes(forecasts, outcomes, scope='task_success'):
    if scope!='task_success':
        raise ValueError('Task outcomes cannot label action or world-model-confidence forecasts')
    labels = {}
    for item in outcomes:
        label = item if isinstance(item, VerifiedOutcome) else VerifiedOutcome(**item)
        key = (label.run_id, label.task_id)
        if key in labels:
            raise ValueError('Duplicate outcome identity')
        labels[key] = label
    seen_ids, seen_methods, rows, missing = set(), set(), [], []
    for item in forecasts:
        f = item if isinstance(item, SuccessForecast) else SuccessForecast(**item)
        key = (f.run_id, f.task_id)
        method = (*key, f.source, f.scope)
        if f.forecast_id in seen_ids or method in seen_methods:
            raise ValueError('Duplicate forecast identity or method per task')
        seen_ids.add(f.forecast_id)
        seen_methods.add(method)
        label = labels.get(key)
        if label:
            for name in ('trajectory_id', 'task_group', 'benchmark_version', 'environment_version', 'tool_version'):
                if getattr(f, name) != getattr(label, name):
                    raise ValueError('Forecast/outcome identity or version mismatch: ' + name)
            if timestamp(f.forecast_timestamp) >= timestamp(label.outcome_timestamp):
                raise ValueError('Forecast must precede verified outcome')
        row = {**asdict(f), 'outcome': label.success if label else None,
               'label_status': label.status if label else 'missing',
               'outcome_timestamp': label.outcome_timestamp if label else None}
        if f.scope != scope:
            raise ValueError('Forecast scopes cannot be pooled')
        (rows if label and label.status == 'verified' else missing).append(row)
    return {'rows': rows, 'unlabeled': missing, 'unused_outcome_count': len(set(labels) - {(r['run_id'], r['task_id']) for r in rows + missing}),
            'coverage': len(rows) / len(forecasts) if forecasts else None}
