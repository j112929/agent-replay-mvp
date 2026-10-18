"""Scenario-cluster bootstrap; repeated seeds never inflate independent N."""
import math
import random
from collections import defaultdict
from statistics import mean


def quantile(values, p):
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    lo = math.floor(position)
    hi = math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def bootstrap(rows, statistic, seed=0, repetitions=400):
    groups = defaultdict(list)
    for row in rows:
        if not isinstance(row.get('task_group'), str):
            raise ValueError('Task group required for bootstrap')
        groups[row['task_group']].append(row)
    if len(groups) < 2:
        return None
    if type(repetitions) is not int or not 100 <= repetitions <= 2000:
        raise ValueError('Bootstrap repetitions must be 100..2000')
    keys = sorted(groups)
    rng = random.Random(seed)
    values = [statistic([r for key in rng.choices(keys, k=len(keys)) for r in groups[key]]) for _ in range(repetitions)]
    values = [v for v in values if v is not None and math.isfinite(v)]
    return [quantile(values, .025), quantile(values, .975)] if len(values) == repetitions else None


def paired_effect(baseline, candidate, field, seed=0, min_groups=20):
    def index(rows):
        result = {}
        for row in rows:
            key = (row['task_group'], row['trial_seed'])
            if key in result:
                raise ValueError('Duplicate paired task/trial')
            value = row.get(field)
            if type(value) not in (int, float, bool) or not math.isfinite(value):
                raise ValueError('Missing or invalid paired metric')
            result[key] = row
        return result
    a, b = index(baseline), index(candidate)
    if not a or set(a) != set(b):
        raise ValueError('Paired scenario/trial sets differ')
    rows = [{'task_group': k[0], 'delta': b[k][field] - a[k][field]} for k in sorted(a)]
    ci = bootstrap(rows, lambda rr: mean(r['delta'] for r in rr), seed)
    groups = sorted({r['task_group'] for r in rows})
    # Two-sided paired sign randomization over scenario-mean effects; screen only.
    grouped = [mean(r['delta'] for r in rows if r['task_group'] == g) for g in groups]
    observed = abs(mean(grouped))
    rng = random.Random(seed)
    if len(groups) <= 12:
        values = [abs(mean(d * (1 if mask & (1 << i) else -1) for i, d in enumerate(grouped))) for mask in range(2**len(groups))]
        p = sum(v >= observed - 1e-12 for v in values) / len(values)
    else:
        p = (1 + sum(abs(mean(d * rng.choice((-1, 1)) for d in grouped)) >= observed - 1e-12 for _ in range(999))) / 1000
    return {'paired_samples': len(rows), 'independent_groups': len(groups), 'effect': mean(r['delta'] for r in rows),
            'confidence_interval': ci, 'p_value': p,
            'decision': 'INCONCLUSIVE' if len(groups) < min_groups or ci is None or ci[0] <= 0 <= ci[1] else 'DIFFERENT',
            'method': 'scenario-cluster percentile bootstrap; paired sign randomization'}


def holm(p_values):
    if any(type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1 for p in p_values):
        raise ValueError('Invalid p-values')
    result = [None] * len(p_values)
    previous = 0
    for rank, (index, p) in enumerate(sorted(enumerate(p_values), key=lambda x: x[1])):
        previous = max(previous, min(1, (len(p_values) - rank) * p))
        result[index] = previous
    return result
