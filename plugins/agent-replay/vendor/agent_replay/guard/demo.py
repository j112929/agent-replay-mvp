"""Synthetic coding A/B capture through data-only tools, not a real model agent."""
import json
from pathlib import Path

from ..capture import Capture
from ..security.replay import ToolInterceptor
from ..serialization import envelope
from ..storage import read, save
from .store import GuardStore
from .runner import run

CANARY = 'sk-syntheticGuardDemoToken012345678901'
TASK = {'instruction': 'Repair add(a,b) so it returns a+b', 'test_input': {'a': 2, 'b': 3}}


def capture_candidate(version, directory, metadata=None):
    """Trusted bundled toy code; every tool only returns an explicit observation."""
    expression = 'a + b' if version == 'A' else 'a * b'
    def mocked(capture, name, arguments, response, effect):
        record = {'input': envelope(arguments), 'output': envelope(response),
                  'execution': {'status': 'completed'}, 'tool': {'side_effect': effect}}
        with capture.span(name, kind='tool', input=arguments, tool={'side_effect': effect}) as span:
            value = ToolInterceptor().resolve(record)
            span.set_output(value)
            return value
    with Capture('Coding agent addition regression', Path(directory) / 'traces', input=TASK,
                 durability='sync', metadata={'agent_id': 'coding-demo', 'agent_version': version,
                     'candidate_entrypoint': 'coding-demo', 'model': 'synthetic-no-model',
                     'prompt_version': 'coding-task.v1', 'environment_version': 'memory-only.v1',
                     'environment': {'Authorization': 'Bearer ' + CANARY}, **(metadata or {})}) as capture:
        mocked(capture, 'repo.read_file', {'path': 'math.py'}, {'source': 'return a - b'}, 'read')
        mocked(capture, 'repo.apply_patch', {'path': 'math.py', 'expression': expression},
               {'applied': True, 'simulated': True}, 'write')
        a, b = TASK['test_input']['a'], TASK['test_input']['b']
        # Fixed toy arithmetic, never eval/generated code/filesystem/network.
        actual = a + b if expression == 'a + b' else a * b
        capture.set_output({'test_value': actual, 'expression': expression})
        if actual != 5: capture.fail('Addition task verifier rejected the patch', 'wrong_answer')
    return read(capture.path)


def demo(directory):
    root = Path(directory).resolve(); root.mkdir(parents=True, exist_ok=True)
    store = GuardStore(root / 'project')
    a = store.import_trace(capture_candidate('A', root / 'captures/A'))['id']
    b = store.import_trace(capture_candidate('B', root / 'captures/B'))['id']
    case = store.generate(b, a, {'output': {'test_value': 5, 'expression': 'a + b'}})['case']
    if case['guard']['validation']['status'] != 'validated': raise ValueError('Demo verifier validation failed')
    store.approve(case['id'], 'synthetic-demo-reviewer')
    passing = run(store, case['id'], a, a)
    regression = run(store, case['id'], a, b)
    incomplete = run(store, case['id'], a, None)
    if [x['decision'] for x in (passing, regression, incomplete)] != ['PASS', 'FAIL', 'INCONCLUSIVE']:
        raise ValueError('Demo release decisions were incorrect')
    config = {'directory': str(root / 'project'), 'case': case['id'], 'baseline': a, 'candidate': b}
    save(root / 'ci-input.json', config)
    result = {'synthetic': True, 'real_model_calls': 0, 'external_tool_operations': 0,
        'case_id': case['id'], 'validation': 'validated', 'approval': 'approved',
        'decisions': [x['decision'] for x in (passing, regression, incomplete)],
        'classification': regression['classification'], 'fixture_count': len(case['replay_spec']['fixtures']),
        'canary_leaked': False, 'ci_input': str(root / 'ci-input.json'),
        'report_dir': str(root / 'project/guard/runs' / regression['id'] / 'reports'),
        'limitations': ['Trusted deterministic toy coding agent; no real model/framework integration or hosted SaaS is demonstrated']}
    save(root / 'demo-result.json', result)
    for path in root.rglob('*'):
        if path.is_file() and CANARY in path.read_text(): raise ValueError('Synthetic canary found in demo artifact')
    return result
