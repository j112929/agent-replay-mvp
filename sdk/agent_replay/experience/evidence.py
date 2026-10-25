"""Read and verify C/D evidence without executing project code."""
from ..serialization import digest
from ..schema import validate_trace
from ..experiments import resolve_spec
from .contracts import validate
from .mutation import validate_bundle, check_id
from .verifiers import evaluate_verifier, qualification_current, implementation_digest
from .compiler import replay_spec
from .refund import registry, CANDIDATES


def inspect_run(bundle, report, objects):
    validate_bundle(bundle)
    if report.get('bundle_digest') != digest(bundle):
        raise ValueError('Evaluation report refers to a different scenario bundle')
    source = validate_trace(objects.get(report['source_digest']))
    dataset = validate(objects.get(bundle['failure_dataset_digest']), 'experience-failures')
    parent = validate(objects.get(bundle['parent_cluster_digest']), 'experience-cluster')
    records = {r['id']: r for r in dataset['records']}
    if not set(parent['member_ids']).issubset(records):
        raise ValueError('Parent failures missing from dataset')
    allowed = {records[k]['source'][f] for k in parent['member_ids'] for f in ('trace_digest', 'normalized_digest')}
    if digest(source) not in allowed:
        raise ValueError('Evaluation source differs from parent failures')
    entries = {digest(e['scenario']): e for e in bundle['entries']}
    qualifications, exclusions = {}, []
    for key, entry in sorted(entries.items()):
        scenario = entry['scenario']
        if scenario['parent_cluster_id'] != parent['id'] or scenario['parent_failure_ids'] != sorted(parent['member_ids']):
            raise ValueError('Scenario lineage mismatch')
        q = report.get('qualification', {}).get(scenario['id'])
        reason = None
        if q is None:
            reason = 'Missing verifier qualification'
        else:
            validate(q, 'experience-qualification')
            expected = {'scenario_digest': key, 'environment_digest': digest(entry['environment']),
                        'verifier_digest': digest(entry['verifier']), 'requirement_digest': digest(entry['requirement']),
                        'source_digest': digest(source), 'implementation_digest': implementation_digest()}
            if any(q[k] != v for k, v in expected.items()):
                reason = 'Stale verifier qualification'
            elif q['state'] != 'qualified':
                reason = 'Verifier qualification is ' + q['state']
            else:
                qualification_current(q, entry, source, objects)
                qualifications[key] = q
        if reason:
            exclusions.append({'artifact_digest': key, 'reason': reason})
    rows, seen = [], set()
    suites = report.get('suites')
    if not isinstance(suites, dict) or not suites:
        raise ValueError('Evaluation needs at least one candidate suite')
    for phase, suite in sorted(suites.items()):
        if not isinstance(phase, str) or not isinstance(suite, dict) or not isinstance(suite.get('results'), list):
            raise ValueError('Invalid candidate suite')
        for item in suite['results']:
            key = item.get('scenario_result_digest')
            if key is None:
                scenario_id = item.get('case_id', '').removeprefix('refund-')
                entry = next((e for e in entries.values() if e['scenario']['id'] == scenario_id), None)
                if entry is None or item.get('verdict') not in ('error', 'inconclusive'):
                    raise ValueError('Candidate result is missing bound scenario evidence')
                rows.append({'phase': phase, 'entry': entry, 'result': None, 'trace': None,
                             'verdict': item['verdict'], 'reason': item.get('reason', 'Missing execution evidence')})
                continue
            result = validate(objects.get(key), 'experience-scenario-result')
            check_id(result)
            if key in seen:
                continue  # Exact artifacts repeated in report inputs count once.
            seen.add(key)
            entry = entries.get(result['scenario_digest'])
            if entry is None or result['environment_digest'] != digest(entry['environment']) or result['verifier_digest'] != digest(entry['verifier']):
                raise ValueError('Candidate artifact refers to a different scenario/environment/verifier')
            if result['seed'] != entry['scenario']['seed']:
                raise ValueError('Candidate seed mismatch')
            trace = validate_trace(objects.get(result['trace_digest']))
            candidate = result['candidate']['id']
            if candidate not in CANDIDATES:
                raise ValueError('Unregistered candidate evidence')
            spec = resolve_spec({**replay_spec(entry), 'entrypoint_id': candidate}, {**registry(), '_root': '.'})
            if trace.get('replay', {}).get('spec_id') != digest(spec) or trace['replay'].get('source_digest') != digest(source):
                raise ValueError('Candidate replay provenance mismatch')
            if trace.get('input', {}).get('value') != spec['input_override']:
                raise ValueError('Candidate input differs from scenario')
            if item.get('candidate_trace_id') != trace['id'] or item.get('case_id') != 'refund-' + entry['scenario']['id']:
                raise ValueError('Regression result association mismatch')
            missing_pointer = False
            for evidence in result['evidence']:
                from ..regression.assertions import pointer
                from ..serialization import MISSING
                artifact = objects.get(evidence['artifact_digest'])
                if pointer(artifact, evidence['pointer']) is MISSING:
                    missing_pointer = True
            if result['fidelity'] != trace.get('fidelity') or result['usage'].get('model_calls') != trace.get('fidelity', {}).get('model_calls'):
                raise ValueError('Candidate fidelity or usage mismatch')
            if result['duration_ms'] != item.get('duration_ms'):
                raise ValueError('Candidate duration differs from regression observation')
            decision = evaluate_verifier(trace, entry['verifier'], entry['environment'])
            if result['verdict'] != decision['verdict'] or item.get('verdict') != decision['verdict']:
                raise ValueError('Claimed candidate verdict differs from verifier evidence')
            reason = 'Missing result evidence pointer' if missing_pointer else ''
            if missing_pointer and decision['verdict'] in ('pass', 'fail'):
                raise ValueError('Decisive verdict lacks its declared evidence pointer')
            if result['candidate']['version'] != implementation_digest():
                reason = 'Candidate implementation version is stale'
            rows.append({'phase': phase, 'entry': entry, 'result': result, 'trace': trace,
                         'verdict': decision['verdict'], 'checks': decision['assertions'], 'reason': reason})
    if not rows:
        raise ValueError('Evaluation candidate suite is empty')
    return {'entries': entries, 'source': source, 'dataset': dataset, 'parent': parent,
            'qualifications': qualifications, 'exclusions': exclusions, 'rows': rows}
