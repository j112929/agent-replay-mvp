"""Bounded file imports. OTLP is an explicit file subset, not a collector."""
import datetime as dt
import json
from pathlib import Path
import re

from ..contracts import validate_v2
from ..migrations import to_v2
from ..schema import validate_trace
from ..serialization import canonical, envelope, MISSING
from ..security.sanitizer import Sanitizer, sanitize_artifact, FAILED

MAX_PAYLOAD = 1024 * 1024
MAX_STEPS = 200
FORMATS = ('native', 'generic', 'otlp')
CATEGORIES = {'wrong_answer', 'tool_error', 'tool_loop', 'timeout',
              'environment_error', 'policy_violation', 'agent_error', 'unknown'}


def load_json(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_PAYLOAD + 1)
    if len(raw) > MAX_PAYLOAD:
        raise ValueError('Import exceeds 1 MiB limit')
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError('Malformed JSON document') from None


def checked_json(value):
    try:
        encoded = canonical(value).encode('utf-8')
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise ValueError('Expected finite portable JSON data') from None
    if len(encoded) > MAX_PAYLOAD:
        raise ValueError('Import exceeds 1 MiB limit')
    if not isinstance(value, dict):
        raise ValueError('Import must be a JSON object')
    return value


def execution(value):
    if value not in ('completed', 'error', 'interrupted', 'running'):
        raise ValueError('Invalid imported execution status')
    return value


def generic(raw):
    for key in ('id', 'name', 'started_at', 'status', 'duration_ms', 'steps'):
        if key not in raw:
            raise ValueError('Generic import requires ' + key)
    if not isinstance(raw['steps'], list) or len(raw['steps']) > MAX_STEPS:
        raise ValueError('At most 200 steps are supported')
    metadata = raw.get('metadata', {})
    if not isinstance(metadata, dict):
        raise ValueError('Metadata must be an object')
    trace = {'schema_version': '2.0', 'id': raw['id'], 'name': raw['name'],
        'started_at': raw['started_at'], 'metadata': metadata,
        'input': envelope(raw.get('input', MISSING)),
        'output': envelope(raw.get('output', MISSING)),
        'execution': {'status': execution(raw['status']), 'duration_ms': raw['duration_ms']},
        'steps': [], 'failures': raw.get('failures', []),
        'capture_health': {'state': 'partial', 'gaps': ['Imported supplied boundaries; uninstrumented execution is unknown']},
        'provenance': {'code': {'commit': metadata.get('agent_version'), 'source_available': False}},
        'evaluation': {'verdict': 'not_evaluated'}}
    if raw.get('error'):
        trace['execution']['error'] = raw['error']
    for index, step in enumerate(raw['steps']):
        if not isinstance(step, dict):
            raise ValueError('Each imported step must be an object')
        item = {'id': step.get('id'), 'parent_id': step.get('parent_id'),
            'seq': index + 1, 'name': step.get('name'), 'kind': step.get('kind'),
            'execution': {'status': execution(step.get('status')),
                'start_ms': step.get('start_ms', 0), 'duration_ms': step.get('duration_ms', 0)},
            'input': envelope(step.get('input', MISSING)), 'output': envelope(step.get('output', MISSING))}
        for key in ('boundary_id', 'scope_path', 'occurrence', 'lane_id', 'tool', 'model', 'error'):
            if key in step:
                item[key] = step[key]
        trace['steps'].append(item)
    return trace


def any_value(value):
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError('Unsupported OTLP attribute value')
    key, data = next(iter(value.items()))
    if key == 'stringValue' and isinstance(data, str): return data
    if key == 'boolValue' and type(data) is bool: return data
    if key == 'doubleValue' and type(data) in (float, int): return data
    if key == 'intValue' and isinstance(data, (int, str)) and re.fullmatch(r'-?\d{1,19}', str(data)):
        number = int(data)
        return number if abs(number) <= 9007199254740991 else str(number)
    if key == 'arrayValue' and isinstance(data, dict):
        return [any_value(x) for x in data.get('values', [])]
    if key == 'kvlistValue' and isinstance(data, dict): return attributes(data.get('values', []))
    raise ValueError('Unsupported OTLP attribute value')


def attributes(rows):
    if not isinstance(rows, list) or len(rows) > 200:
        raise ValueError('Invalid OTLP attributes')
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('key'), str) or row['key'] in result:
            raise ValueError('Invalid or duplicate OTLP attribute')
        result[row['key']] = any_value(row.get('value'))
    return result


def nanos(value):
    if not isinstance(value, (int, str)) or not re.fullmatch(r'\d{1,20}', str(value)):
        raise ValueError('Invalid OTLP timestamp')
    return int(value)


def otlp(raw):
    resources = raw.get('resourceSpans')
    if not isinstance(resources, list) or not resources:
        raise ValueError('Expected OTLP resourceSpans')
    spans = []
    for resource in resources:
        attrs = attributes(resource.get('resource', {}).get('attributes', []))
        for scope in resource.get('scopeSpans', []):
            for span in scope.get('spans', []):
                spans.append((span, attrs))
    if not spans or len(spans) > MAX_STEPS:
        raise ValueError('Expected 1–200 OTLP spans')
    ids = {s.get('traceId') for s, _ in spans}
    if len(ids) != 1 or not re.fullmatch(r'[0-9a-fA-F]{32}', next(iter(ids)) or ''):
        raise ValueError('Import exactly one valid OTLP trace')
    if next(iter(ids)) == '0' * 32: raise ValueError('Invalid OTLP trace ID')
    starts = [nanos(s.get('startTimeUnixNano')) for s, _ in spans]
    ends = [nanos(s.get('endTimeUnixNano')) for s, _ in spans]
    if any(e < b for b, e in zip(starts, ends)):
        raise ValueError('Invalid OTLP time range')
    earliest = min(starts)
    steps = []
    roots = []
    metadata = {}
    for span, resource_attributes in sorted(spans, key=lambda pair: nanos(pair[0]['startTimeUnixNano'])):
        sid = span.get('spanId', '')
        if not re.fullmatch(r'[0-9a-fA-F]{16}', sid) or sid == '0' * 16:
            raise ValueError('Invalid OTLP span ID')
        attr = attributes(span.get('attributes', []))
        code = span.get('status', {}).get('code', 0)
        if code not in (0, 1, 2, 'STATUS_CODE_UNSET', 'STATUS_CODE_OK', 'STATUS_CODE_ERROR'):
            raise ValueError('Invalid OTLP status')
        status = 'error' if code in (2, 'STATUS_CODE_ERROR') else 'completed'
        kind = attr.get('agent_replay.kind', 'tool' if 'gen_ai.tool.name' in attr else 'llm' if 'gen_ai.request.model' in attr else 'agent')
        item = {'id': sid.lower(), 'parent_id': span.get('parentSpanId', '').lower() or None,
            'name': attr.get('gen_ai.tool.name', span.get('name')), 'kind': kind, 'status': status,
            'start_ms': (nanos(span['startTimeUnixNano']) - earliest) / 1e6,
            'duration_ms': (nanos(span['endTimeUnixNano']) - nanos(span['startTimeUnixNano'])) / 1e6}
        for target, key in (('input', 'agent_replay.input'), ('output', 'agent_replay.output')):
            if key in attr:
                data = attr[key]
                if isinstance(data, str):
                    try: data = json.loads(data)
                    except (ValueError, RecursionError): pass
                item[target] = data
        # Keep decoded structured attributes: credential keys must be recognizable.
        item['tool'] = {'side_effect': attr.get('agent_replay.side_effect', 'unknown'), 'attributes': attr}
        steps.append(item)
        if item['parent_id'] is None:
            roots.append(item)
            metadata = dict(resource_attributes)
    if len(roots) != 1:
        raise ValueError('OTLP trace requires one complete root span')
    root = roots[0]
    metadata.update(root.get('tool', {}).get('attributes', {}))
    for target, keys in {'agent_id': ('agent_replay.agent_id', 'service.name'),
                         'agent_version': ('agent_replay.agent_version', 'service.version'),
                         'model': ('gen_ai.request.model',), 'prompt_version': ('agent_replay.prompt_version',)}.items():
        for key in keys:
            if key in metadata: metadata[target] = metadata[key]; break
    doc = {'id': next(iter(ids)).lower(), 'name': root['name'],
        'started_at': dt.datetime.fromtimestamp(earliest / 1e9, dt.timezone.utc).isoformat(),
        'status': 'error' if any(x['status'] == 'error' for x in steps) else 'completed',
        'duration_ms': (max(ends) - earliest) / 1e6, 'steps': steps, 'metadata': metadata}
    for key in ('input', 'output'):
        if key in root: doc[key] = root[key]
    return generic(doc)


def normalize(payload, format='native', org_id='local', project_id='default', sensitive_fields=()):
    checked_json(payload)
    if format not in FORMATS: raise ValueError('Unsupported import format')
    try:
        converted = to_v2(validate_trace(payload)) if format == 'native' else generic(payload) if format == 'generic' else otlp(payload)
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
        raise ValueError('Invalid ' + format + ' schema, timestamps or causal relationships') from None
    if len(converted['steps']) > MAX_STEPS: raise ValueError('At most 200 steps are supported')
    sanitizer = Sanitizer(sensitive_fields=sensitive_fields)
    trace = sanitizer.sanitize(converted)
    if sanitizer.failed or FAILED in canonical(trace) or trace.get('privacy', {}).get('status') == 'failed_closed':
        raise ValueError('Import rejected: sanitization could not be guaranteed')
    old_privacy = trace.get('privacy', {})
    privacy = sanitizer.metadata()
    if isinstance(old_privacy, dict):
        old_findings = old_privacy.get('redacted_fields', [])
        if isinstance(old_findings, list):
            privacy['redacted_fields'] = old_findings + privacy['redacted_fields']
            privacy['redacted_field_count'] = len(privacy['redacted_fields'])
        if old_privacy.get('replay_compatibility') == 'fixtures_required':
            privacy['replay_compatibility'] = 'fixtures_required'
            privacy['limitations'] = old_privacy.get('limitations', [])
    trace['privacy'] = privacy
    try: trace = validate_v2(sanitize_artifact(trace))
    except (ValueError, TypeError, KeyError): raise ValueError('Invalid sanitized trajectory schema') from None
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', trace['id']):
        raise ValueError('Import ID must be a portable identifier (max 80 characters)')
    metadata = trace.setdefault('metadata', {})
    if not isinstance(metadata, dict): raise ValueError('Metadata must be an object')
    for key in ('org_id', 'organization_id', 'project_id', 'guard'):
        metadata.pop(key, None)
    for key in ('org_id', 'organization_id', 'project_id'):
        trace.pop(key, None)
    metadata['guard'] = {'org_id': org_id, 'project_id': project_id, 'import_format': format}
    if metadata.get('agent_id'):
        metadata['candidate_entrypoint'] = metadata['agent_id']
    trace.setdefault('provenance', {'code': {'commit': metadata.get('agent_version'), 'source_available': False}})
    for step in trace['steps']:
        if step.get('parent_id') is not None and not isinstance(step['parent_id'], str):
            raise ValueError('Parent reference must be a span identifier')
        if not isinstance(step.get('scope_path', []), list) or not all(isinstance(x, str) for x in step.get('scope_path', [])):
            raise ValueError('Invalid causal scope')
        if step.get('lane_id') is not None and not isinstance(step['lane_id'], str): raise ValueError('Invalid lane identifier')
        for field in ('tool', 'model'):
            if field in step and not isinstance(step[field], dict): raise ValueError('Invalid imported ' + field + ' metadata')
    if not isinstance(trace.get('failures', []), list): raise ValueError('Failures must be a list')
    if trace['execution']['status'] == 'error' and not trace.get('failures'):
        trace['failures'] = [{'category': 'agent_error', 'message': 'Imported execution failed'}]
    for failure in trace.get('failures', []):
        if not isinstance(failure, dict): raise ValueError('Failure must be an object')
        if failure.get('category') not in CATEGORIES: failure['category'] = 'unknown'
    return trace
