"""Declarative refund verifiers with qualification bound to immutable evidence."""
import copy
from pathlib import Path
from ..serialization import digest, MISSING
from ..regression.assertions import evaluate, pointer
from .contracts import validate
from .refund import VERSION, NEGATIVES

EVALUATOR = 'refund-assertions.v1'


def implementation_digest():
    # Qualification is invalid after evaluator, simulator or execution code changes.
    root = Path(__file__).resolve().parents[1]
    files = ['experience/refund.py', 'experience/verifiers.py', 'experience/mutation.py',
             'experience/compiler.py', 'runtime/runner.py', 'runtime/policy.py',
             'regression/assertions.py', 'regression/runner.py', 'experiments.py']
    import hashlib
    return digest({name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files})


def generate_verifier(requirement, environment):
    validate(requirement, 'experience-refund-requirement')
    validate(environment, 'experience-environment')
    order_id = requirement['order_id']
    amount = min(requirement['requested_minor'], requirement['approved_minor'],
                 requirement['paid_minor'] - requirement['refunded_minor'])
    initial = environment['initial_state']
    expected_order = {k: requirement[k] for k in ('approved_minor', 'paid_minor', 'refunded_minor')}
    if initial.get('currency') != requirement['currency']:
        raise ValueError('Environment currency differs from the trusted requirements')
    if initial.get('orders', {}).get(order_id) != expected_order or initial.get('witness_amount_minor') != amount:
        raise ValueError('Environment state does not satisfy trusted requirements')
    if set(initial.get('orders', {})) != {order_id, 'other-order'}:
        raise ValueError('Environment order scope mismatch')
    final = copy.deepcopy(initial['orders'])
    final[order_id]['refunded_minor'] += amount
    select = {'boundary_id': 'payments.refund', 'kind': 'tool'}
    rules = [{'id': 'completed', 'op': 'execution_completed'},
             {'id': 'no-error', 'op': 'no_unhandled_error'},
             {'id': 'exactly-one-refund', 'op': 'count', 'select': select, 'expected': 1}]
    for name, field, op, expected in [('correct-order', 'order_id', 'equals', order_id),
            ('approved-bound', 'amount_minor', 'lte', requirement['approved_minor']),
            ('remaining-bound', 'amount_minor', 'lte', requirement['paid_minor'] - requirement['refunded_minor']),
            ('requested-bound', 'amount_minor', 'lte', requirement['requested_minor']),
            ('exact-amount', 'amount_minor', 'equals', amount)]:
        rules.append({'id': name, 'op': op, 'select': select, 'path': '/input/value/' + field, 'expected': expected})
    for name, field, expected in [('environment', 'environment_digest', digest(environment)),
            ('adapter', 'adapter_version', VERSION), ('initial-state', 'initial', initial),
            ('actual-ledger', 'ledger', [{'order_id': order_id, 'amount_minor': amount}]),
            ('actual-state-transition', 'orders', final)]:
        rules.append({'id': name, 'op': 'equals', 'path': '/metadata/environment_state/' + field, 'expected': expected})
    core = {'schema_version': 'experience.verifier.v1', 'evaluator_id': EVALUATOR,
            'assertions': rules, 'requirement_digests': [digest(requirement)],
            'qualification': {'state': 'draft', 'evidence_digests': []}}
    return validate({'id': digest(core), **core})


def evaluate_verifier(trace, verifier, environment):
    """No observed environment state means inconclusive, never successful."""
    try:
        validate(verifier, 'experience-verifier')
        if verifier['evaluator_id'] != EVALUATOR:
            raise ValueError('Unsupported evaluator')
        if trace.get('capture_health', {}).get('state') != 'complete':
            return {'verdict': 'inconclusive', 'assertions': [], 'reason': 'Incomplete capture'}
        state = pointer(trace, '/metadata/environment_state')
        if state is MISSING or not isinstance(state, dict) or any(k not in state for k in ('initial', 'orders', 'ledger', 'adapter_version', 'environment_digest')):
            return {'verdict': 'inconclusive', 'assertions': [], 'reason': 'Missing state evidence'}
        if state['environment_digest'] != digest(environment) or state['adapter_version'] != VERSION:
            return {'verdict': 'inconclusive', 'assertions': [], 'reason': 'Incompatible environment evidence'}
        errors = [(s.get('error') or s.get('execution', {}).get('error') or {}).get('type') for s in trace.get('steps', [])]
        errors.append((trace.get('execution', {}).get('error') or {}).get('type'))
        if any(e in ('ReplayMismatch', 'RecordedError') for e in errors):
            return {'verdict': 'inconclusive', 'assertions': [], 'reason': 'Replay boundary unavailable'}
        checks = evaluate(trace, verifier['assertions'])
        verdict = 'pass' if all(x['verdict'] == 'pass' for x in checks) and not trace.get('failures') else 'fail'
        return {'verdict': verdict, 'assertions': checks, 'reason': ''}
    except (ValueError, KeyError, TypeError) as exc:
        return {'verdict': 'error', 'assertions': [], 'reason': str(exc)}


def qualification_current(report, entry, source, objects):
    """Recompute witness decisions; a JSON 'qualified' label grants no authority."""
    validate(report, 'experience-qualification')
    expected = {'scenario_digest': digest(entry['scenario']), 'environment_digest': digest(entry['environment']),
                'verifier_digest': digest(entry['verifier']), 'requirement_digest': digest(entry['requirement']),
                'source_digest': digest(source), 'implementation_digest': implementation_digest()}
    if any(report[k] != value for k, value in expected.items()):
        raise ValueError('Stale qualification bindings')
    names = {'positive', *NEGATIVES}
    if set(report['witnesses']) != names or report['state'] != 'qualified' or not report['stable']:
        raise ValueError('Verifier is not qualified')
    evidence = set()
    for name in sorted(names):
        witness = report['witnesses'][name]
        trace = objects.get(witness['trace_digest'])
        evidence.add(witness['trace_digest'])
        decision = evaluate_verifier(trace, entry['verifier'], entry['environment'])
        expected_status = 'error' if name == 'partial_execution' else 'completed'
        if trace.get('execution', {}).get('status') != expected_status or witness.get('execution_status') != expected_status:
            raise ValueError('Qualification witness did not execute the intended counterexample')
        if name == 'partial_execution':
            expected_ledger = next(a['expected'] for a in entry['verifier']['assertions'] if a['id'] == 'actual-ledger')
            if pointer(trace, '/metadata/environment_state/ledger') != expected_ledger or trace['execution'].get('error', {}).get('type') != 'RuntimeError':
                raise ValueError('Partial execution witness did not reach the payment')
        if decision['verdict'] != ('pass' if name == 'positive' else 'fail') or witness['verdict'] != decision['verdict']:
            raise ValueError('Qualification witness does not meet the mandatory decision')
        from .compiler import replay_spec
        from ..experiments import resolve_spec
        from .refund import registry
        expected_spec = resolve_spec({**replay_spec(entry), 'entrypoint_id': name}, {**registry(), '_root': '.'})
        if witness.get('spec_digest') != digest(expected_spec) or trace.get('replay', {}).get('spec_id') != digest(expected_spec):
            raise ValueError('Witness replay specification mismatch')
        regression = objects.get(witness['regression_digest'])
        if regression.get('candidate_trace_id') != trace.get('id') or regression.get('verdict') != decision['verdict']:
            raise ValueError('Witness regression evidence mismatch')
        replay = trace.get('replay', {})
        if replay.get('source_digest') != digest(source):
            raise ValueError('Witness source mismatch')
        if trace.get('input', {}).get('value') != {**entry['scenario']['input'], '_environment': entry['environment']}:
            raise ValueError('Witness scenario input mismatch')
        if any(evaluate_verifier(trace, entry['verifier'], entry['environment']) != decision for _ in range(100)):
            raise ValueError('Verifier instability')
    if evidence != set(report['evidence_digests']):
        raise ValueError('Qualification evidence inventory mismatch')
    return True
