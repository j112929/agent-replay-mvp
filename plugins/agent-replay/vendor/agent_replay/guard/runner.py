"""Evidence-only evaluation and CI report, reusing the existing case evaluator."""
import os
import html
import json
from pathlib import Path
import tempfile
import time
import uuid

from ..storage import read, save
from ..runtime.fixtures import Fixtures
from ..regression.runner import run_case
from ..regression.cases import case_digest
from ..regression.reports import write_reports
from ..security.replay import ToolInterceptor, playback, ReplayDenied
from ..serialization import digest, canonical
from .generator import complete, identity
from .store import require_approval, reference


def candidate_compatible(case, trace):
    if trace.get('replay'): raise ValueError('Historical playback cannot prove a candidate version')
    agent_id, version = identity(trace)
    if agent_id != case['guard']['compatibility']['agent_id']: raise ValueError('Candidate agent identity mismatch')
    if not complete(trace['input']) or digest(trace['input']) != case['guard']['compatibility']['input_digest']:
        raise ValueError('Candidate task input is unavailable or different')
    if not complete(trace['output']): raise ValueError('Candidate outcome is unavailable or redacted')
    if trace['execution']['status'] not in ('completed', 'error'):
        raise ValueError('Candidate execution is incomplete')
    fixtures = Fixtures(case['replay_spec']['fixtures'])
    occurrences = {}
    for step in trace['steps']:
        if step['kind'] != 'tool': continue
        if not complete(step['input']): raise ValueError('Candidate tool input is unavailable')
        key = (step['name'], tuple(step.get('scope_path', [])), step.get('lane_id'))
        occurrences[key] = occurrences.get(key, 0) + 1
        response = fixtures.resolve({**step, 'occurrence': occurrences[key], 'scope_path': step.get('scope_path', [])})
        if response is None: raise ValueError('Unknown tool/input has no explicit recorded fixture')
        recorded = ToolInterceptor().resolve(step)
        if canonical(response.get('value')) != canonical(recorded):
            raise ValueError('Candidate tool response differs from the approved recording')
    return version


def run(store, case_id, baseline_id=None, candidate_id=None, version=None, *,
        require_evidence=False, release_evidence=None, evidence_key=None):
    start = time.monotonic(); run_id = str(uuid.uuid4())
    with store.locked():
        path = store.case_path(case_id, version); case = read(path)
        store.ensure('runs', 4 * 1024 * 1024)  # conservative reservation for bounded traces and diffs
        parent = store.root / 'guard/runs'
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.pending-', dir=parent) as staged:
            directory = Path(staged)
            validation = None
            if require_evidence:
                from .evidence import verify
                try:
                    validation = verify(store, case, baseline_id, candidate_id, release_evidence, evidence_key)
                except (ValueError, OSError, KeyError, TypeError, ReplayDenied):
                    validation = {'verified': False, 'reason': 'Release evidence rejected: check signing trust, target, freshness, integrity and approval'}
            gate = evaluate_to_directory(store, path, case, directory, run_id, start, baseline_id, candidate_id, validation)
            store.ensure_storage(0)
            os.replace(directory, parent / run_id)
        return gate


def evaluate_to_directory(store, path, case, directory, run_id, start, baseline_id, candidate_id, release_validation=None):
    case_id = case['id']
    results = []
    versions = {}; reasons = []
    for role, ident in (('baseline', baseline_id), ('candidate', candidate_id)):
        try:
            if role == 'candidate' and release_validation is not None and not release_validation['verified']:
                raise ValueError(release_validation['reason'])
            require_approval(case, path, store.root)
            if not case['guard'].get('enabled'): raise ValueError('Required regression case is disabled')
            if not ident: raise ValueError('Required ' + role + ' trajectory is missing')
            target = store.trace_path(ident); trace = read(target)
            versions[role] = candidate_compatible(case, trace)
            project = {'_root': str(store.root), 'entrypoints': {case['entrypoint_id']: {}}, 'suites': {}}
            result = run_case(path, project, directory / role, candidate_reference=reference(target, path.parent))
            if result['verdict'] in ('error', 'inconclusive'):
                reasons.append(role + ': ' + result.get('reason', 'Evaluation unavailable'))
        except (ValueError, OSError, KeyError, TypeError, ReplayDenied) as exc:
            # No raw payloads or callback exceptions are used by this path.
            result = {'schema_version': '1.0', 'id': str(uuid.uuid4()), 'case_id': case_id,
                      'verdict': 'inconclusive', 'assertions': [], 'reason': str(exc)}
            reasons.append(role + ': ' + str(exc))
        result['role'] = role; results.append(result)
    before, after = (r['verdict'] for r in results)
    classification = ('inconclusive' if reasons else 'regression' if before == 'pass' and after == 'fail'
                      else 'improvement' if before == 'fail' and after == 'pass'
                      else 'unchanged_pass' if after == 'pass' else 'unchanged_fail')
    decision = 'INCONCLUSIVE' if reasons else 'FAIL' if after == 'fail' else 'PASS'
    if release_validation and release_validation['verified'] and release_validation['cost_usd'] > release_validation['max_cost_usd']:
        reasons.append('Candidate cost exceeds the approved release budget')
        if decision == 'PASS': classification = 'cost_regression'
        if decision != 'INCONCLUSIVE': decision = 'FAIL'
    gate = {'schema_version': '1.0', 'id': run_id, 'case_id': case_id,
        'case_version': case['guard']['version'], 'case_digest': case_digest(case),
        'decision': decision, 'blocking': decision != 'PASS', 'classification': classification,
        'versions': versions, 'reasons': reasons or (['Approved critical verifier failed'] if decision == 'FAIL' else []),
        'release_eligible': decision == 'PASS' and release_validation is not None and release_validation['verified'],
        'release_evidence': release_validation or {'verified': False, 'reason': 'Captured-evidence evaluation only; no release attestation'},
        'replay_mode': 'RECORDED_REPLAY', 'execution_kind': 'CAPTURED_CANDIDATE_EVALUATION',
        'external_operations': 0, 'model_calls': 0, 'fixture_digest': case['guard']['fixture_digest'],
        'duration_ms': (time.monotonic() - start) * 1000,
        'limitations': ['Does not execute arbitrary agent code; release signatures trust the operator-controlled CI signer',
                        'Verifier discrimination is validated only on the supplied positive/negative pair'],
        'results': results, 'exit_code': {'PASS': 0, 'FAIL': 1, 'INCONCLUSIVE': 3}[decision]}
    # Replay source separately for inspection; it never becomes candidate evidence.
    from .store import resolve_reference
    try:
        historical = read(resolve_reference(case['source'], path.parent, store.root))
        save(directory / 'playback.json', playback(historical))
        gate['playback_path'] = 'playback.json'
    except (ValueError, OSError): gate['playback_path'] = None
    save(directory / 'gate.json', gate)
    write_reports(results, directory / 'reports')
    # Extend the existing report with gate context; trace bodies stay in its evidence.
    context = {k: gate[k] for k in ('decision', 'classification', 'versions', 'replay_mode',
        'execution_kind', 'release_eligible', 'release_evidence', 'reasons', 'limitations')}
    header = '<h1>Agent Regression Guard: ' + decision + '</h1><pre>' + html.escape(json.dumps(context, indent=2)) + '</pre>'
    header += '<p><a href="gate.json">Machine-readable gate</a></p>'
    if gate['playback_path']: header += '<p><a href="../playback.json">Simulated recorded playback</a></p>'
    report = directory / 'reports/index.html'
    report.write_text(report.read_text().replace('<h1>Agent Regression Report</h1>', header))
    save(directory / 'reports/gate.json', gate)
    return gate
