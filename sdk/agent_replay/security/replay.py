"""Data-only replay. This controller has no executable tool/agent capability."""
import copy
from enum import Enum
import uuid
from ..contracts import validate_v2
from ..migrations import to_v2
from .sanitizer import sanitize_artifact, REDACTED, FAILED


class ReplayDenied(ValueError):
    pass


class ReplayMode(str, Enum):
    MOCK_REPLAY = 'MOCK_REPLAY'
    SANDBOX_REPLAY = 'SANDBOX_REPLAY'


class Operation(str, Enum):
    READ_ONLY = 'READ_ONLY'
    STATE_MUTATING = 'STATE_MUTATING'
    UNKNOWN = 'UNKNOWN'


def classify(step):
    effect = step.get('tool', {}).get('side_effect', 'unknown')
    return Operation.READ_ONLY if effect == 'read' else Operation.STATE_MUTATING if effect == 'write' else Operation.UNKNOWN


def isolation_status():
    return {'available': False, 'network': 'no executable capability',
        'filesystem': 'no executable capability', 'credentials': 'not acquired',
        'reason': 'No verified OS isolation backend is configured; executable replay is disabled'}


def deny_execution():
    raise ReplayDenied('Executable replay is disabled: no verified sandbox. Use TRACE_PLAYBACK or recorded mocks; no host-execution fallback.')


class ToolInterceptor:
    """Accepts data only, not callables; classification never grants execution."""
    def resolve(self, step=None, policy='MOCK'):
        if policy not in ('MOCK', 'DENY', 'ALLOW_IN_SANDBOX'):
            raise ReplayDenied('Unknown tool policy')
        if policy != 'MOCK':
            raise ReplayDenied('Tool execution denied; sandbox unavailable')
        if not isinstance(step, dict) or 'output' not in step:
            raise ReplayDenied('Unknown tool without explicit recorded response')
        output = step['output']
        if not isinstance(output, dict) or output.get('present') is not True or output.get('replayability') != 'complete':
            raise ReplayDenied('Recorded response unavailable/redacted; isolated authorized fixture required')
        import json
        if any(marker in json.dumps(output) for marker in (REDACTED, FAILED, '[CAPTURE_ERROR]')):
            raise ReplayDenied('Redacted values cannot be used as executable mocks')
        if step.get('execution', {}).get('status') != 'completed':
            raise ReplayDenied('Recorded tool failed; inspect the recorded exception')
        return copy.deepcopy(output['value'])


def playback(trace, mode='MOCK_REPLAY', kind='TRACE_PLAYBACK'):
    if mode != 'MOCK_REPLAY' or kind != 'TRACE_PLAYBACK':
        deny_execution()
    # Validate after sanitizing historical imports. No project imports or subprocesses.
    result = validate_v2(to_v2(sanitize_artifact(trace)))
    source = result['id']
    result['id'] = str(uuid.uuid4())
    limitations = []
    blocked = []
    for step in result['steps']:
        if step['kind'] == 'tool':
            blocked.append({'step_id': step['id'], 'operation': classify(step).value,
                            'decision': 'MOCK', 'real_execution': 'DENY'})
        if any(step.get(k, {}).get('replayability') != 'complete' for k in ('input', 'output')):
            limitations.append({'step_id': step['id'], 'reason': 'Unavailable or sanitized observation; not reusable as a complete mock'})
    result['replay'] = {'source_trace_id': source, 'mode': 'recorded', 'scope': 'agent',
        'application_validated': False, 'execution_kind': kind, 'replay_mode': mode,
        'simulated': True, 'external_operations': 0, 'model_calls': 0,
        'limitations': limitations, 'blocked_operations': blocked}
    result['security'] = {'sandbox': isolation_status(), 'tool_policy': 'MOCK',
        'external_network': 'DENY', 'production_credentials': 'DENY'}
    result['evaluation'] = {'verdict': 'not_evaluated', 'reason': 'Playback does not evaluate a new agent version'}
    return result
