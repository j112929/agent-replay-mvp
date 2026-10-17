"""Actual offline capture → trusted CI signature → single-use release decision.

The throwaway signer authenticates synthetic toy code, not production execution.
"""
import json
from pathlib import Path
import secrets
import subprocess
import sys

from agent_replay.guard.demo import demo, capture_candidate
from agent_replay.guard.evidence import issue, capture_context, attest
from agent_replay.guard.runner import run
from agent_replay.guard.store import GuardStore
from agent_replay.storage import read, save

CONFIGURATION = {'model': 'synthetic-no-model', 'prompt_version': 'coding-task.v1',
                 'tools_version': 'memory-tools.v1', 'environment_version': 'memory-only.v1'}


def main(directory):
    root = Path(directory).resolve()
    if root.exists(): raise ValueError('Use a fresh evidence directory')
    demo(root)
    inputs = read(root / 'ci-input.json'); store = GuardStore(inputs['directory'])
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    key = secrets.token_hex(32); gates = []
    for version in ('A', 'B'):
        challenge = issue(store, inputs['case'], inputs['baseline'], commit, version,
                          CONFIGURATION, 'synthetic-ci', .01)
        trace = capture_candidate(version, root / ('fresh-' + version),
                                  capture_context(challenge, 'synthetic-run-' + version, CONFIGURATION))
        bundle = attest(challenge, trace, key, Path.cwd(), 'synthetic-run-' + version, CONFIGURATION, 0)
        save(root / ('evidence-' + version + '.json'), bundle)
        candidate = store.import_trace(bundle['trace'])['id']
        gate = run(store, inputs['case'], inputs['baseline'], candidate,
                   require_evidence=True, release_evidence=read(root / ('evidence-' + version + '.json')), evidence_key=key)
        gates.append(gate)
    repeated = run(store, inputs['case'], inputs['baseline'], candidate,
                   require_evidence=True, release_evidence=bundle, evidence_key=key)
    assert [g['decision'] for g in gates] + [repeated['decision']] == ['PASS', 'FAIL', 'INCONCLUSIVE']
    assert [g['release_eligible'] for g in gates] == [True, False]
    assert all(g['external_operations'] == g['model_calls'] == 0 for g in gates)
    result = {'synthetic': True, 'commit': commit, 'decisions': ['PASS', 'FAIL', 'INCONCLUSIVE'],
              'release_eligible': [True, False, False], 'external_operations': 0, 'model_calls': 0,
              'trust': 'throwaway HMAC signer for bundled toy code only'}
    save(root / 'release-summary.json', result)
    for path in root.rglob('*.json'):
        if key in path.read_text(): raise ValueError('Signer key leaked into an artifact')
    print(json.dumps(result))


if __name__ == '__main__': main(sys.argv[1])
