"""Task-aligned comparisons and explicit, fail-closed CI quality gates."""
from dataclasses import asdict
import math
from .contracts import INFRA_FAILURES
from .metrics import aggregate
from .store import RunStore
from ..serialization import digest


def delta(before, after):
    difference = after - before if before is not None and after is not None else None
    relative = (after / before - 1) if difference is not None and before else (0 if difference == 0 else None)
    return {'baseline': before, 'candidate': after, 'delta': difference, 'relative_change': relative}


def metric_deltas(before, after):
    return {'success_rate': delta(before['success_rate'], after['success_rate']),
        **{name: {stat: delta(before['distributions'][name][stat], after['distributions'][name][stat])
            for stat in ('mean', 'median', 'p90', 'p95')}
           for name in ('duration_ms', 'steps', 'tokens', 'cost_usd')}}


def compare_runs(baseline, candidate):
    a, left = RunStore(baseline).load()
    b, right = RunStore(candidate).load()
    if a['benchmark'] != b['benchmark'] or a['metadata'].get('adapter') != b['metadata'].get('adapter'):
        raise ValueError('Cannot compare different benchmark adapters or suites')
    at, bt = ({t['id']: t for t in run['tasks']} for run in (a, b))
    aa, bb = ({r.task_id: r for r in rows} for rows in (left, right))
    matched = sorted(aa.keys() & bb.keys())
    changed = [ident for ident in matched if digest(at[ident]) != digest(bt[ident])]
    comparable = [ident for ident in matched if ident not in changed]
    classifications = {'newly_passing': [], 'newly_failing': [], 'unchanged_pass': [], 'unchanged_fail': []}
    tasks = []
    for ident in comparable:
        before, after = aa[ident], bb[ident]
        key = ('unchanged_pass' if before.success else 'newly_passing') if after.success else (
            'newly_failing' if before.success else 'unchanged_fail')
        classifications[key].append(ident)
        tasks.append({'task_id': ident, 'baseline': asdict(before), 'candidate': asdict(after),
                      'classification': {'newly_passing': 'improvement', 'newly_failing': 'regression'}.get(key, key)})
    before, after = aggregate([aa[i] for i in comparable]), aggregate([bb[i] for i in comparable])
    tags = {}
    for tag in sorted(set(before['by_tag']) | set(after['by_tag'])):
        x, y = before['by_tag'][tag], after['by_tag'][tag]
        changes = metric_deltas(x, y)
        tags[tag] = {**changes, 'regression': changes['success_rate']['delta'] < 0}
    return {'schema_version': 'benchmark-comparison.v1', 'baseline_id': a['id'], 'candidate_id': b['id'],
        **classifications, 'tasks': tasks, 'added_tasks': sorted(bb.keys() - aa.keys()),
        'removed_tasks': sorted(aa.keys() - bb.keys()), 'changed_tasks': changed,
        'matched_count': len(comparable), 'metrics': metric_deltas(before, after), 'by_tag': tags,
        'baseline': before, 'candidate': after,
        'all_tasks': {'baseline': aggregate(left), 'candidate': aggregate(right)},
        'infrastructure_errors': {'baseline': [r.task_id for r in left if r.failure_category in INFRA_FAILURES],
                                  'candidate': [r.task_id for r in right if r.failure_category in INFRA_FAILURES]}}


def quality_gate(comparison, max_success_drop=0, max_new_failures=0, max_cost_increase=None):
    for name, value in (('max_success_drop', max_success_drop), ('max_new_failures', max_new_failures), ('max_cost_increase', max_cost_increase)):
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise ValueError(name + ' must be finite and nonnegative')
    if max_success_drop > 1 or type(max_new_failures) is not int:
        raise ValueError('Success drop must be <= 1 and new failures must be an integer')
    reasons = []
    if not comparison['matched_count'] or any(comparison[k] for k in ('added_tasks', 'removed_tasks', 'changed_tasks')):
        reasons.append('Task coverage or task definitions differ; gate requires identical task sets')
    if any(comparison['infrastructure_errors'].values()):
        reasons.append('Verifier/environment infrastructure errors require investigation')
    success = comparison['metrics']['success_rate']['delta']
    if success is None or success < -max_success_drop - 1e-12:
        reasons.append('Success-rate drop exceeded threshold or is unavailable')
    if len(comparison['newly_failing']) > max_new_failures:
        reasons.append('New regression count exceeded threshold')
    if max_cost_increase is not None:
        costs = comparison['metrics']['cost_usd']['mean']
        coverage = [comparison[k]['distributions']['cost_usd'] for k in ('baseline', 'candidate')]
        if any(c['missing'] or not c['count'] for c in coverage):
            reasons.append('Cost gate cannot evaluate missing cost measurements')
        elif costs['baseline'] == 0:
            if costs['candidate'] > 0:
                reasons.append('Cost increased from a zero baseline')
        elif costs['relative_change'] > max_cost_increase + 1e-12:
            reasons.append('Cost increase exceeded threshold')
    return {'passed': not reasons, 'reasons': reasons,
        'thresholds': {'max_success_drop': max_success_drop, 'max_new_failures': max_new_failures,
                       'max_cost_increase': max_cost_increase}, 'comparison': comparison}
