import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_replay.guard.demo import capture_candidate
from agent_replay.guard.evidence import issue, attest, capture_context
from agent_replay.guard.runner import run
from agent_replay.guard.store import GuardStore
from agent_replay.security.replay import playback
from agent_replay.storage import read, save


CONFIG = {'model': 'synthetic', 'prompt_version': 'addition.v1',
          'tools_version': 'memory.v1', 'environment_version': 'toy.v1'}


@contextmanager
def cwd(path):
    old = Path.cwd()
    os.chdir(path)
    try: yield
    finally: os.chdir(old)


class ReleaseEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.repo = self.root / 'checkout'; self.repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'core.hooksPath=/dev/null',
            '-c', 'user.name=Release Test', '-c', 'user.email=release@example.invalid',
            'commit', '--allow-empty', '-qm', 'Synthetic clean checkout'], check=True)
        self.commit = subprocess.check_output(['git', '-C', str(self.repo), 'rev-parse', 'HEAD'], text=True).strip()
        self.store = GuardStore(self.root / 'controller', 'org-a', 'project-a')
        self.good = self.store.import_trace(capture_candidate('A', self.root / 'good'))['id']
        self.bad = self.store.import_trace(capture_candidate('B', self.root / 'bad'))['id']
        self.case = self.store.generate(self.bad, self.good, {'output': {'test_value': 5, 'expression': 'a + b'}})['case']
        self.store.approve(self.case['id'], 'reviewer')
        self.key = secrets.token_hex(32)

    def challenge(self, version='A', **changes):
        return issue(self.store, self.case['id'], self.good, self.commit, version, CONFIG,
                     'trusted-ci', changes.get('max_cost', 1), changes.get('ttl', 900))

    def bundle(self, challenge, version='A', cost=0):
        with cwd(self.repo):
            trace = capture_candidate(version, self.root / 'candidate', capture_context(challenge, 'run-123', CONFIG))
        value = attest(challenge, trace, self.key, self.repo, 'run-123', CONFIG, cost)
        self.store.import_trace(value['trace'])
        return value

    def release(self, bundle, key=None, **changes):
        return run(self.store, self.case['id'], changes.get('baseline', self.good),
                   changes.get('candidate', bundle['manifest']['trace_id']), require_evidence=True,
                   release_evidence=bundle, evidence_key=self.key if key is None else key)

    def test_real_capture_signed_release_and_single_use(self):
        proof = self.bundle(self.challenge())
        gate = self.release(proof)
        self.assertEqual((gate['decision'], gate['release_eligible'], gate['exit_code']), ('PASS', True, 0))
        self.assertEqual(gate['release_evidence']['commit'], self.commit)
        self.assertEqual(self.release(proof)['decision'], 'INCONCLUSIVE')
        self.assertFalse(run(self.store, self.case['id'], self.good, self.good)['release_eligible'])
        reports = list((self.store.root / 'guard/runs').glob('*/reports/gate.json'))
        self.assertTrue(any(read(p)['release_eligible'] for p in reports))
        for path in self.store.root.rglob('*.json'):
            self.assertNotIn(self.key, path.read_text())

    def test_regression_and_cost_failure_remain_blocking(self):
        for version, cost, reason in [('B', 0, 'regression'), ('A', 2, 'budget')]:
            proof = self.bundle(self.challenge(version), version, cost)
            gate = self.release(proof)
            self.assertEqual((gate['decision'], gate['release_eligible'], gate['exit_code']), ('FAIL', False, 1))
            self.assertTrue(gate['release_evidence']['verified'])
            if reason == 'budget': self.assertIn('budget', ' '.join(gate['reasons']))

    def test_wrong_key_tampered_manifest_and_missing_cost_do_not_consume(self):
        proof = self.bundle(self.challenge())
        broken = copy.deepcopy(proof); broken['manifest']['commit'] = 'f' * 40
        absent = copy.deepcopy(proof); absent['manifest'].pop('cost_usd')
        for bundle, key in [(proof, '0' * 64), (broken, self.key), (absent, self.key), (proof, '')]:
            result = self.release(bundle, key=key)
            self.assertEqual((result['decision'], result['release_eligible']), ('INCONCLUSIVE', False))
        self.assertEqual(self.release(proof)['decision'], 'PASS')

    def test_changed_trace_and_wrong_baseline_are_rejected(self):
        proof = self.bundle(self.challenge())
        self.assertEqual(self.release(proof, baseline=self.bad)['decision'], 'INCONCLUSIVE')
        target = self.store.trace_path(proof['manifest']['trace_id']); original = read(target)
        changed = copy.deepcopy(original); changed['output']['value']['test_value'] = 6; save(target, changed)
        self.assertEqual(self.release(proof)['decision'], 'INCONCLUSIVE')
        save(target, original)
        self.assertEqual(self.release(proof)['decision'], 'PASS')

    def test_expired_proof_and_changed_case_are_rejected(self):
        proof = self.bundle(self.challenge(ttl=30))
        with patch('agent_replay.guard.evidence.time.time', return_value=time.time() + 60):
            self.assertEqual(self.release(proof)['decision'], 'INCONCLUSIVE')
        self.store.control(self.case['id'], 'disable', 'reviewer')
        self.assertEqual(self.release(proof)['decision'], 'INCONCLUSIVE')
        self.store.control(self.case['id'], 'enable', 'reviewer')
        self.assertEqual(self.release(proof)['decision'], 'PASS')

    def test_signer_refuses_old_playback_dirty_or_wrong_checkout(self):
        challenge = self.challenge()
        old = read(self.store.trace_path(self.good))
        with self.assertRaises(ValueError): attest(challenge, old, self.key, self.repo, 'run-123', CONFIG, 0)
        proof = self.bundle(challenge)
        with self.assertRaises(ValueError): attest(challenge, playback(proof['trace']), self.key, self.repo, 'run-123', CONFIG, 0)
        (self.repo / 'untracked.txt').write_text('dirty')
        with self.assertRaises(ValueError): attest(challenge, proof['trace'], self.key, self.repo, 'run-123', CONFIG, 0)
        (self.repo / 'untracked.txt').unlink()
        wrong = {**challenge, 'commit': 'f' * 40}
        with self.assertRaises(ValueError): attest(wrong, proof['trace'], self.key, self.repo, 'run-123', CONFIG, 0)

    def test_configuration_invalid_cost_and_cross_project_cannot_pass(self):
        challenge = self.challenge()
        for config in [{}, {**CONFIG, 'model': 'other'}]:
            with self.assertRaises(ValueError): capture_context(challenge, 'run-123', config)
        for cost in [None, True, float('nan'), -1]:
            with self.assertRaises(ValueError): self.bundle(challenge, cost=cost)
        proof = self.bundle(challenge)
        other = GuardStore(self.store.root, 'org-b', 'project-a')
        gate = run(other, self.case['id'], self.good, proof['manifest']['trace_id'],
                   require_evidence=True, release_evidence=proof, evidence_key=self.key)
        self.assertEqual(gate['decision'], 'INCONCLUSIVE')
        self.assertEqual(self.release(proof)['decision'], 'PASS')

    def test_cli_release_exit_codes_and_key_never_printed(self):
        # CLI defaults to a local/default tenant. Its project must match the challenge.
        self.store.org_id = 'local'; self.store.project_id = 'default'
        proof = self.bundle(self.challenge()); path = self.root / 'proof.json'; save(path, proof)
        command = [sys.executable, '-m', 'agent_replay.cli', 'guard', 'release', self.case['id'],
            '--directory', str(self.store.root), '--baseline', self.good,
            '--candidate', proof['manifest']['trace_id'], '--evidence', str(path)]
        env = {**os.environ, 'GUARD_EVIDENCE_SIGNING_KEY': self.key,
               'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'sdk')}
        for expected in [0, 3]:
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, expected, result.stderr + result.stdout)
            self.assertEqual(json.loads(result.stdout)['release_eligible'], expected == 0)
            self.assertNotIn(self.key, result.stdout + result.stderr)

    def test_concurrent_release_consumes_one_challenge_once(self):
        proof = self.bundle(self.challenge())
        with ThreadPoolExecutor(max_workers=2) as pool:
            gates = list(pool.map(lambda _: self.release(proof), range(2)))
        self.assertEqual(sorted(g['decision'] for g in gates), ['INCONCLUSIVE', 'PASS'])

    def test_signer_rejects_partial_capture_and_missing_boundaries(self):
        challenge = self.challenge(); proof = self.bundle(challenge)
        for health in [{'state': 'partial'}, {'state': 'complete', 'dropped_events': 1}, []]:
            changed = copy.deepcopy(proof['trace']); changed['capture_health'] = health
            with self.assertRaises(ValueError): attest(challenge, changed, self.key, self.repo, 'run-123', CONFIG, 0)
        changed = copy.deepcopy(proof['trace']); changed['steps'][0]['output'] = {'present': False, 'replayability': 'unsupported'}
        with self.assertRaises(ValueError): attest(challenge, changed, self.key, self.repo, 'run-123', CONFIG, 0)

    def test_service_bridge_uses_only_operator_project_trust(self):
        from agent_replay.saas_worker import execute
        keyfile = self.root / 'private-operator.json'
        keyfile.write_text(json.dumps({'org-a/project-a': {'runner_id': 'trusted-ci', 'secret': self.key}}))
        request = {'case_id': self.case['id'], 'baseline': self.good, 'commit': self.commit,
                   'candidate_version': 'A', 'configuration': CONFIG, 'max_cost_usd': 1,
                   'runner_id': 'untrusted-request-override', 'secret': '0' * 64}
        with patch.dict(os.environ, {'GUARD_EVIDENCE_KEYS_FILE': str(keyfile)}):
            challenge = execute(self.store, 'release_challenge', request)
            self.assertEqual(challenge['runner_id'], 'trusted-ci')
            proof = self.bundle(challenge)
            release = {'case_id': self.case['id'], 'baseline': self.good,
                       'candidate': proof['manifest']['trace_id'], 'evidence': proof}
            other = GuardStore(self.store.root, 'org-b', 'project-a')
            with self.assertRaises(ValueError): execute(other, 'release', release)
            keyfile.write_text(json.dumps({'org-a/project-a': {'runner_id': 'revoked-runner', 'secret': self.key}}))
            with self.assertRaises(ValueError): execute(self.store, 'release', release)
            keyfile.write_text(json.dumps({'org-a/project-a': {'runner_id': 'trusted-ci', 'secret': self.key}}))
            self.assertTrue(execute(self.store, 'release', release)['release_eligible'])
