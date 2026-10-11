"""Deterministic draft generation; a failed output cannot define its own oracle."""
from ..regression.assertions import evaluate, validate_rules
from ..security.replay import ToolInterceptor, ReplayDenied
from ..serialization import canonical, digest
from ..security.sanitizer import REDACTED, FAILED


def complete(value):
    return (isinstance(value, dict) and value.get('present') is True
            and value.get('replayability') == 'complete'
            and not any(marker in canonical(value) for marker in (REDACTED, FAILED, '[CAPTURE_ERROR]')))


def identity(trace):
    metadata = trace.get('metadata', {})
    name = metadata.get('agent_id')
    version = metadata.get('agent_version')
    if not isinstance(name, str) or not name or not isinstance(version, str) or not version or any(marker in name+version for marker in (REDACTED,FAILED)):
        raise ValueError('Agent ID and agent version metadata are required')
    return name, version


def fixtures_from(traces):
    fixtures = {}
    for trace in traces:
        occurrences = {}
        for step in trace['steps']:
            if step['kind'] != 'tool': continue
            if any(marker in canonical([step['name'],step.get('scope_path',[])]) for marker in (REDACTED,FAILED)):
                raise ValueError('Tool identity is unavailable or redacted')
            if not complete(step['input']):
                raise ValueError('Required tool input is unavailable or redacted')
            try: value = ToolInterceptor().resolve(step)
            except ReplayDenied:
                raise ValueError('Required recorded tool response is unavailable or redacted') from None
            boundary = (step['name'], tuple(step.get('scope_path', [])), step.get('lane_id'))
            occurrences[boundary] = occurrences.get(boundary, 0) + 1
            selector = {'kind': 'tool', 'name': step['name'], 'input_digest': digest(step['input']),
                        'occurrence': occurrences[boundary], 'scope_path': step.get('scope_path', []),
                        'lane_id': step.get('lane_id')}
            key = digest(selector)
            if key in fixtures and canonical(fixtures[key]['response']['value']) != canonical(value):
                raise ValueError('Ambiguous recorded responses for the same call')
            fixtures[key] = {'id': key, 'selector': selector, 'consume': 'once',
                             'response': {'kind': 'return', 'value': value}}
    return {'schema_version': '1.0', 'fixtures': list(fixtures.values())}


def propose(source, positive=None, expected=None):
    draft = {'assertions': [], 'fixtures': {'schema_version': '1.0', 'fixtures': []},
        'validation': {'status': 'incomplete', 'confidence': 'example_pair_only',
                       'positive_pass': False, 'negative_fail': False}, 'reason': None}
    try:
        if source.get('replay'):
            raise ValueError('A historical playback cannot be the source failure')
        if source['execution']['status'] != 'error' and not source.get('failures'):
            raise ValueError('A supported explicit failure is required')
        if positive is None: raise ValueError('Known positive trajectory is required')
        if positive.get('replay') or positive['execution']['status'] != 'completed' or positive.get('failures'):
            raise ValueError('Positive evidence must be a completed candidate without failures')
        agent_id, _ = identity(source)
        if identity(positive)[0] != agent_id: raise ValueError('Positive agent identity mismatch')
        if not complete(source['input']) or not complete(positive['input']):
            raise ValueError('Task input is unavailable or redacted')
        if digest(source['input']) != digest(positive['input']):
            raise ValueError('Positive evidence describes a different task input')
        if not complete(positive['output']): raise ValueError('Positive outcome is unavailable or redacted')
        if not isinstance(expected, dict) or set(expected) not in ({'output'}, {'assertions'}):
            raise ValueError('Explicit expected output or declarative assertions are required')
        rules = expected.get('assertions') if 'assertions' in expected else [
            {'id': 'expected-output', 'op': 'equals', 'path': '/output/value', 'expected': expected['output']}]
        validate_rules(rules)
        if not any(r['op'] not in ('execution_completed', 'no_unhandled_error') for r in rules):
            raise ValueError('A business outcome assertion is required')
        if any(marker in canonical(rules) for marker in (REDACTED, FAILED)):
            raise ValueError('Expected behavior contains unavailable sensitive data')
        fixtures = fixtures_from((source, positive))
        positive_results = evaluate(positive, rules)
        negative_results = evaluate(source, rules)
        valid = all(r['verdict'] == 'pass' for r in positive_results) and any(r['verdict'] == 'fail' for r in negative_results)
        draft.update(assertions=rules, fixtures=fixtures,
            validation={'status': 'validated' if valid else 'incomplete', 'confidence': 'example_pair_only',
                'positive_pass': all(r['verdict'] == 'pass' for r in positive_results),
                'negative_fail': any(r['verdict'] == 'fail' for r in negative_results),
                'positive_assertions': positive_results, 'negative_assertions': negative_results},
            reason=None if valid else 'Verifier must pass the positive and reject the original negative example')
    except ValueError as exc:
        # Our controlled messages contain no payloads or converter exception text.
        draft['reason'] = str(exc)
    return draft
