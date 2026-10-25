"""Evidence-based refund metrics; unavailable measurements remain null."""
from collections import Counter, defaultdict
import html
import json
from pathlib import Path
from ..serialization import digest
from ..storage import save
from .contracts import validate
from .evidence import inspect_run
from .stages import Stages

VERSION = 'refund-evaluation.v1'


def measurement(numerator, denominator, *, scope='synthetic-refund'):
    return {'value': numerator / denominator if denominator else None,
            'numerator': numerator, 'denominator': denominator,
            'state': 'measured' if denominator else 'unmeasured', 'scope': scope}


def evaluate(bundle, report, *, directory, baseline=None, persist=True):
    objects = Stages(directory).objects
    evidence = inspect_run(bundle, report, objects)
    rows = evidence['rows']
    counts = Counter(row['verdict'] for row in rows)
    grouped = defaultdict(list)
    modes, candidates = Counter(), {}
    for row in rows:
        result = row['result']
        if result is None:
            continue
        identity = digest(result['candidate'])
        key = (result['scenario_digest'], identity)
        grouped[key].append(row['verdict'])
        candidate = candidates.setdefault(identity, {'candidate': result['candidate'], 'verdicts': Counter(), 'duration_ms': 0, 'model_calls': 0})
        candidate['verdicts'][row['verdict']] += 1
        candidate['duration_ms'] += result['duration_ms']
        candidate['model_calls'] += result['usage']['model_calls']
        if row['verdict'] == 'fail':
            failed = sorted(check['id'] for check in row['checks'] if check['verdict'] == 'fail')
            if not failed:
                failed = ['captured_failure']
            modes[digest({'failed_assertions': failed})] += 1
    qualified = evidence['qualifications']
    witnesses = [w for q in qualified.values() for name, w in q['witnesses'].items() if name != 'positive']
    per_scenario = defaultdict(dict)
    for (scenario_key, candidate_key), verdicts in grouped.items():
        per_scenario[scenario_key][candidate_key] = set(verdicts)
    cross_candidate = [v for v in per_scenario.values() if len(v) >= 2]
    repeated = [values for values in grouped.values() if len(values) >= 2]
    metrics = {
        'execution_rate': measurement(sum(r['trace'] is not None for r in rows), len(rows)),
        'decisive_verdict_rate': measurement(counts['pass'] + counts['fail'], len(rows)),
        'success_rate': measurement(counts['pass'], counts['pass'] + counts['fail']),
        'qualification_rate': measurement(len(qualified), len(evidence['entries'])),
        'mandatory_negative_rejection_rate': measurement(sum(w['verdict'] == 'fail' for w in witnesses), len(witnesses)),
        'identical_evidence_stability': measurement(sum(q['stable'] for q in qualified.values()), len(qualified)),
        'repeat_candidate_verdict_consistency': measurement(sum(len(set(v)) == 1 for v in repeated), len(repeated)),
        'cross_candidate_failure_reproduction': measurement(sum(sum('fail' in verdicts for verdicts in v.values()) >= 2 for v in cross_candidate), len(cross_candidate)),
        'new_failure_mode_rate': measurement(0, 0),
        'cross_model_failure_reproduction': measurement(0, 0),
        'held_out_task_success_improvement': measurement(0, 0),
    }
    baseline_digest = None
    if baseline is not None:
        validate(baseline, 'experience-evaluation')
        # Baselines must be a stored evaluation whose original run evidence is rechecked.
        stored = objects.get(digest(baseline))
        baseline_bundle = objects.get(stored['bundle_digest'])
        baseline_report = objects.get(stored['report_digest'])
        recomputed = evaluate(baseline_bundle, baseline_report, directory=directory, persist=False)
        if recomputed != baseline or baseline['baseline_digest'] is not None:
            raise ValueError('Baseline evaluation is not reproducible or already relative to another baseline')
        baseline_digest = digest(baseline)
        new_modes = set(modes) - set(baseline['observed_failure_modes'])
        metrics['new_failure_mode_rate'] = measurement(len(new_modes), len(modes), scope='synthetic-assertion-signatures')
    for candidate in candidates.values():
        candidate['verdicts'] = dict(candidate['verdicts'])
        candidate['success_rate'] = measurement(candidate['verdicts'].get('pass', 0),
            candidate['verdicts'].get('pass', 0) + candidate['verdicts'].get('fail', 0))
    value = validate({'schema_version': 'experience.evaluation.v1', 'implementation_version': VERSION,
        'bundle_digest': digest(bundle), 'report_digest': digest(report), 'baseline_digest': baseline_digest,
        'scenario_count': len(evidence['entries']), 'result_count': len(rows), 'verdict_counts': dict(counts),
        'metrics': metrics, 'candidates': [candidates[k] for k in sorted(candidates)],
        'operator_coverage': dict(sorted(Counter(e['scenario']['mutation']['operator_id'] for e in evidence['entries'].values()).items())),
        'observed_failure_modes': dict(sorted(modes.items())), 'exclusions': evidence['exclusions'],
        'measurement_notes': ['Failure modes are failed-assertion combinations, not adjudicated root causes.',
            'Cross-model replication requires independent live model evidence; refund candidates make no model calls.',
            'Held-out improvement requires a leakage-free held-out evaluation and training intervention.',
            'Durations are recorded observations, not controlled performance comparisons.']})
    if persist:
        objects.put(bundle); objects.put(report); objects.put(value)
    return value


def write_evaluation(value, directory):
    validate(value, 'experience-evaluation')
    directory = Path(directory).resolve()
    save(directory / 'evaluation.json', value)
    rows = []
    for name, m in value['metrics'].items():
        display = 'unmeasured' if m['value'] is None else f"{100*m['value']:.1f}% ({m['numerator']}/{m['denominator']})"
        rows.append('<tr><td>' + html.escape(name) + '</td><td>' + display + '</td></tr>')
    candidate_rows = []
    for candidate in value['candidates']:
        rate = candidate['success_rate']['value']
        candidate_rows.append('<tr><td>' + html.escape(candidate['candidate']['id']) + '</td><td>' + ('unmeasured' if rate is None else f'{100*rate:.1f}%') + '</td><td>' + html.escape(json.dumps(candidate['verdicts'])) + '</td><td>' + str(candidate['model_calls']) + '</td></tr>')
    notes = ''.join('<li>' + html.escape(n) + '</li>' for n in value['measurement_notes'])
    content = '<!doctype html><meta charset="utf-8"><title>Refund evaluation</title><style>body{font:16px system-ui;max-width:1000px;margin:40px auto;padding:20px}td{padding:10px;border-bottom:1px solid #ddd}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>Refund evaluation</h1><p>' + str(value['scenario_count']) + ' scenarios · ' + str(value['result_count']) + ' unique results · synthetic evidence</p><table>' + ''.join(rows) + '</table><h2>Per candidate</h2><p>Overall success pools all candidates; it does not measure training improvement.</p><table><tr><th>Candidate</th><th>Success</th><th>Verdicts</th><th>Model calls</th></tr>' + ''.join(candidate_rows) + '</table><ul>' + notes + '</ul><details><summary>Evidence</summary><pre>' + html.escape(json.dumps(value, ensure_ascii=False, indent=2)) + '</pre></details>'
    (directory / 'evaluation.html').write_text(content, encoding='utf-8')
    return {'json': str(directory / 'evaluation.json'), 'html': str(directory / 'evaluation.html')}
