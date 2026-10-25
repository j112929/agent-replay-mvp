"""Compile refund sidecars to existing registered replay/regression machinery."""
import hashlib
import html
import json
from pathlib import Path
from ..schema import validate_trace
from ..serialization import digest
from ..storage import read, save
from ..project import load_project
from ..regression.runner import run_case, run_suite
from ..experiments import resolve_spec
from .contracts import validate
from .mutation import validate_bundle
from .refund import CANDIDATES, NEGATIVES, registry
from .stages import Stages
from .verifiers import evaluate_verifier, implementation_digest, qualification_current


def replay_spec(entry):
    return {'scope': 'agent', 'policy': 'frozen',
            'input_override': {**entry['scenario']['input'], '_environment': entry['environment']},
            'limits': entry['environment']['limits'],
            'determinism': {'rng_seed': entry['scenario']['seed'], 'clock_fixture': 0,
                            'state_adapter_id': 'refund-reset', 'state_snapshot_adapter_id': 'refund-snapshot',
                            'state_cleanup_adapter_id': 'refund-cleanup'},
            'tool_rules': [{'selector': {'boundary_id': 'orders.lookup', 'kind': 'tool'},
                            'mode': 'local', 'implementation_id': 'refund-lookup'},
                           {'selector': {'boundary_id': 'payments.refund', 'kind': 'tool'},
                            'mode': 'local', 'implementation_id': 'refund-payment'}]}


def _immutable(path, value):
    if path.exists() and read(path) != value:
        raise ValueError('Compiled artifact changed: ' + str(path))
    if not path.exists():
        save(path, value)


def compile_bundle(bundle, source, directory, *, candidate='fixed', qualifications=None):
    """Drafts may execute for qualification, but only qualified cases are promoted."""
    validate_bundle(bundle)
    validate_trace(source)
    if candidate not in CANDIDATES:
        raise ValueError('Unknown registered refund candidate')
    directory = Path(directory).resolve()
    objects = Stages(directory).objects
    dataset = validate(objects.get(bundle['failure_dataset_digest']), 'experience-failures')
    cluster = validate(objects.get(bundle['parent_cluster_digest']), 'experience-cluster')
    records = {r['id']: r for r in dataset['records']}
    if any(entry['scenario']['parent_cluster_id'] != cluster['id'] or entry['scenario']['parent_failure_ids'] != sorted(cluster['member_ids']) for entry in bundle['entries']):
        raise ValueError('Scenario lineage differs from parent cluster')
    if not set(cluster['member_ids']).issubset(records):
        raise ValueError('Unknown parent failure')
    allowed_sources = {records[key]['source'][field] for key in cluster['member_ids'] for field in ('trace_digest','normalized_digest')}
    if digest(source) not in allowed_sources:
        raise ValueError('Source does not belong to the parent failure cluster')
    source_key = objects.put(source)
    paths = []
    for entry in bundle['entries']:
        qualification = None
        if qualifications is not None:
            qualification = qualifications.get(entry['scenario']['id'])
            if qualification is None:
                raise ValueError('Missing scenario qualification')
            qualification_current(qualification, entry, source, objects)
        key = digest({'entry': entry, 'source': source_key, 'candidate': candidate,
                      'implementation': implementation_digest(), 'qualification': qualification})
        root = directory / 'compiled' / key
        project = registry()
        case = {'schema_version': '1.0', 'id': 'refund-' + entry['scenario']['id'],
                'title': entry['scenario']['mutation']['operator_id'], 'tags': ['offline', 'refund', 'experience'],
                'source': {'trace_path': 'source.json', 'sha256': ''}, 'entrypoint_id': candidate,
                'replay_spec': replay_spec(entry), 'assertions': entry['verifier']['assertions']}
        _immutable(root / 'source.json', source)
        case['source']['sha256'] = hashlib.sha256((root / 'source.json').read_bytes()).hexdigest()
        _immutable(root / 'agent-replay.json', project)
        _immutable(root / 'refund.case.json', case)
        _immutable(root / 'scenario.json', entry)
        if qualification is not None:
            _immutable(root / 'qualification.json', qualification)
        paths.append({'case': str(root / 'refund.case.json'), 'project': str(root / 'agent-replay.json'),
                      'scenario_id': entry['scenario']['id'], 'qualified': qualification is not None})
    return paths


def execute_compiled(compiled, entry, directory, objects):
    project = load_project(compiled['project'])
    outcome = run_case(compiled['case'], project, directory)
    objects.put(outcome)
    trace = None
    if outcome.get('experiment_id'):
        experiment = read(Path(directory) / 'runs' / 'experiments' / (outcome['experiment_id'] + '.json'))
        trace = experiment.get('trace')
    if trace is None:
        return {'verdict': 'error', 'reason': outcome.get('reason', 'No candidate trace'),
                'regression': outcome, 'trace': None}
    decision = evaluate_verifier(trace, entry['verifier'], entry['environment'])
    result = persist_result(entry, read(compiled['case'])['entrypoint_id'], trace, outcome, directory, objects)
    return {**decision, 'regression': outcome, 'trace': trace, 'result': result}


def persist_result(entry, candidate, trace, outcome, directory, objects):
    trace_key = objects.put(trace)
    decision = evaluate_verifier(trace, entry['verifier'], entry['environment'])
    core = {'schema_version': 'experience.scenario-result.v1', 'scenario_digest': digest(entry['scenario']),
            'environment_digest': digest(entry['environment']), 'verifier_digest': digest(entry['verifier']),
            'candidate': {'id': candidate, 'version': implementation_digest()},
            'seed': entry['scenario']['seed'], 'trace_digest': trace_key, 'verdict': decision['verdict'],
            'evidence': [{'artifact_digest': trace_key, 'pointer': '/metadata/environment_state'}],
            'fidelity': trace['fidelity'], 'duration_ms': outcome['duration_ms'],
            'usage': {'model_calls': trace['fidelity']['model_calls']}}
    result = validate({'id': digest(core), **core})
    objects.put(result)
    save(Path(directory) / 'scenario-result.json', result)
    return result


def qualify(bundle, source, directory):
    validate_bundle(bundle)
    directory = Path(directory).resolve()
    stages = Stages(directory)
    reports = {}
    for entry in bundle['entries']:
        cache_key = digest({'scenario': entry['scenario'], 'source': digest(source), 'implementation': implementation_digest()})
        cache_path = directory / 'qualifications' / (cache_key + '.json')
        if cache_path.exists():
            prior = read(cache_path)
            if prior.get('state') == 'qualified':
                qualification_current(prior, entry, source, stages.objects)
                reports[entry['scenario']['id']] = prior
                continue
        witnesses, stable = {}, True
        singleton = {**bundle, 'entries': [entry]}
        for name in ('positive', *NEGATIVES):
            compiled = compile_bundle(singleton, source, directory, candidate=name)[0]
            attempt = Path(compiled['case']).parent / 'qualification-runs'
            executed = execute_compiled(compiled, entry, attempt, stages.objects)
            trace = executed['trace']
            key = stages.objects.put(trace) if trace is not None else stages.objects.put({'error': executed['reason']})
            decision = {k: executed[k] for k in ('verdict', 'assertions', 'reason')} if trace is not None else {'verdict': 'error'}
            if trace is not None:
                stable = stable and all(evaluate_verifier(trace, entry['verifier'], entry['environment']) == decision for _ in range(100))
            else:
                stable = False
            spec = resolve_spec({**replay_spec(entry), 'entrypoint_id': name}, load_project(compiled['project']))
            witnesses[name] = {'trace_digest': key, 'verdict': executed['verdict'], 'spec_digest': digest(spec),
                               'regression_digest': stages.objects.put(executed['regression']),
                               'execution_status': trace.get('execution', {}).get('status') if trace is not None else 'unavailable'}
        accepted = stable and all(witnesses[name]['execution_status'] == ('error' if name == 'partial_execution' else 'completed')
                                  and witnesses[name]['verdict'] == ('pass' if name == 'positive' else 'fail')
                                  for name in witnesses)
        report = validate({'schema_version': 'experience.qualification.v1',
            'scenario_digest': digest(entry['scenario']), 'environment_digest': digest(entry['environment']),
            'verifier_digest': digest(entry['verifier']), 'requirement_digest': digest(entry['requirement']),
            'source_digest': digest(source), 'implementation_digest': implementation_digest(),
            'state': 'qualified' if accepted else 'rejected', 'repeats': 100, 'stable': stable,
            'witnesses': witnesses, 'evidence_digests': sorted({w['trace_digest'] for w in witnesses.values()})})
        stages.objects.put(report)
        save(cache_path, report)
        if accepted:
            qualification_current(report, entry, source, stages.objects)
        reports[entry['scenario']['id']] = report
    return reports


def refund_loop(bundle, source, directory):
    """Qualify first, then reproduce failure/fix/regression through existing suites."""
    validate_bundle(bundle)
    directory = Path(directory).resolve()
    qualifications = qualify(bundle, source, directory)
    if any(q['state'] != 'qualified' for q in qualifications.values()):
        raise ValueError('Refund verifier qualification rejected')
    suites = {}
    for candidate in ('broken', 'fixed', 'broken'):
        phase = 'before' if not suites else 'after' if candidate == 'fixed' else 'regressed'
        compiled = compile_bundle(bundle, source, directory, candidate=candidate, qualifications=qualifications)
        results = []
        for item, entry in zip(compiled, bundle['entries']):
            root = directory / phase / item['scenario_id']
            # Preserve the existing suite exit codes, JUnit and HTML reports.
            suite = run_suite([item['case']], load_project(item['project']), root)
            result = suite['results'][0]
            if result.get('experiment_id'):
                experiment = read(root / 'runs' / 'experiments' / (result['experiment_id'] + '.json'))
                trace = experiment.get('trace')
                if trace:
                    Stages(directory).objects.put(trace)
                    decision = evaluate_verifier(trace, entry['verifier'], entry['environment'])
                    result['verifier_verdict'] = decision['verdict']
                    scenario_result = persist_result(entry, candidate, trace, result, root, Stages(directory).objects)
                    result['scenario_result_digest'] = digest(scenario_result)
            results.append(result)
        suites[phase] = {'results': results, 'exit_code': 2 if any(r['verdict'] == 'error' for r in results)
                         else 3 if any(r['verdict'] == 'inconclusive' for r in results)
                         else 1 if any(r['verdict'] == 'fail' for r in results) else 0}
    codes = [suites[phase]['exit_code'] for phase in ('before', 'after', 'regressed')]
    if codes != [1, 0, 1] or any(r.get('verifier_verdict') != r['verdict'] for phase in suites.values() for r in phase['results']):
        raise ValueError('Refund loop did not produce consistent fail/pass/fail')
    total = len(bundle['entries'])
    negatives = [w for q in qualifications.values() for name, w in q['witnesses'].items() if name != 'positive']
    all_results = [r for phase in suites.values() for r in phase['results']]
    report = {'scope': 'synthetic-refund', 'scenario_count': len(bundle['entries']),
              'qualified_count': len(qualifications), 'stability_repeats': 100, 'external_calls': 0,
              'qualification': qualifications, 'suites': suites, 'exit_codes': codes,
              'metrics': {'execution_rate': sum('candidate_trace_id' in r for r in all_results) / len(all_results),
                          'qualification_rate': sum(q['state'] == 'qualified' for q in qualifications.values()) / total,
                          'mandatory_negative_rejection_rate': sum(w['verdict'] == 'fail' for w in negatives) / len(negatives),
                          'fixed_success_rate': sum(r['verdict'] == 'pass' for r in suites['after']['results']) / total},
              'bundle_digest': digest(bundle), 'source_digest': digest(source),
              'limitations': ['Synthetic refund fixtures; production novelty and cross-model reproducibility unmeasured.',
                              'No RL training or held-out success improvement measured.']}
    save(directory / 'report.json', report)
    write_refund_report(report, bundle, directory / 'report.html')
    return report


def write_refund_report(report, bundle, path):
    rows = []
    for index, entry in enumerate(bundle['entries']):
        name = html.escape(entry['scenario']['mutation']['operator_id'])
        verdicts = [report['suites'][phase]['results'][index]['verdict'] for phase in ('before', 'after', 'regressed')]
        cells = ''.join('<td>' + html.escape(v) + '</td>' for v in verdicts)
        rows.append('<tr><td>' + name + '</td>' + cells + '<td>qualified · 7/7 negatives rejected</td></tr>')
    data = html.escape(json.dumps(report, ensure_ascii=False, indent=2))
    content = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
               '<title>Refund experience acceptance</title><style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:20px;color:#172033;background:#f5f7fa}'
               'table{border-collapse:collapse;width:100%;background:white}td,th{text-align:left;padding:12px;border-bottom:1px solid #ddd}'
               'pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}h1{color:#185d45}</style>'
               '<h1>Refund: fail → pass → fail</h1><p>' + str(report['scenario_count']) + ' runnable scenarios · '
               + str(report['qualified_count']) + ' qualified verifiers · 100 identical-evidence stability repetitions · 0 external calls</p>'
               '<p>Each run uses fresh refund state. Verification checks the actual ledger and state transition.</p>'
               '<table><tr><th>Mutation</th><th>Broken</th><th>Fixed</th><th>Regressed</th><th>Verifier</th></tr>'
               + ''.join(rows) + '</table><p>Scope: synthetic refund acceptance. Production novelty, cross-model reproducibility '
               'and RL training improvement remain unmeasured.</p><details><summary>Evidence and digests</summary><pre>'
               + data + '</pre></details></html>')
    Path(path).write_text(content, encoding='utf-8')
