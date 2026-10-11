import hashlib
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent_replay.security.access import AccessControl
from agent_replay.server import make_handler
from test_guard import example, CANARY


class GuardApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tokens = {key: key * 40 for key in ('a', 'b', 'd', 'v')}
        roles = [('a', 'alpha', 'ADMIN'), ('d', 'alpha', 'DEVELOPER'), ('v', 'alpha', 'VIEWER'), ('b', 'beta', 'ADMIN')]
        access = AccessControl(self.root, {'identities': [{'token_sha256': hashlib.sha256(self.tokens[k].encode()).hexdigest(),
            'org_id': org, 'role': role, 'subject': k} for k, org, role in roles], 'guard_limits': {'imports': 3}})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.root, Path(__file__).resolve().parents[1] / 'dist', {'_root': str(self.root)}, access))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.cleanup_server)

    def cleanup_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def request(self, path, key='a', body=None):
        req = Request(f'http://127.0.0.1:{self.server.server_port}/api/v1/' + path,
            json.dumps(body).encode() if body is not None else None,
            headers={'Authorization': 'Bearer ' + self.tokens[key], 'Content-Type': 'application/json'})
        with urlopen(req) as response: return json.load(response)

    def denied(self, path, key, body=None, code=403):
        with self.assertRaises(HTTPError) as error: self.request(path, key, body)
        self.assertEqual(error.exception.code, code); error.exception.close()

    def test_full_http_flow_tenant_roles_results_and_audit(self):
        a = self.request('guard/import', 'd', {'trace': example('A'), 'format': 'generic', 'org_id': 'beta'})['id']
        b = self.request('guard/import', 'd', {'trace': example(), 'format': 'generic'})['id']
        generated = self.request('guard/cases', 'd', {'source_id': b, 'positive_id': a, 'expected': {'output': {'fixed': True}}, 'approved': True})
        case_id = generated['case']['id']; self.assertEqual(generated['review']['state'], 'draft')
        self.assertEqual(self.request('traces')['items'][0]['metadata']['guard']['org_id'], 'alpha')
        self.assertEqual(self.request('guard/cases', 'b')['items'], [])
        self.denied('guard/cases/' + case_id, 'b', code=404)
        self.denied('guard/cases/' + case_id + '/approve', 'd', {})
        self.denied('guard/import', 'v', {'trace': example('C'), 'format': 'generic'})
        self.assertEqual(self.request('guard/cases/' + case_id, 'v')['review']['state'], 'draft')
        self.request('guard/cases/' + case_id + '/approve', body={'reviewer': 'forged-client-user'})
        self.assertEqual(self.request('guard/cases/' + case_id)['review']['reviewer'], 'a')
        gate = self.request('guard/runs', 'd', {'case_id': case_id, 'baseline_id': a, 'candidate_id': b})
        self.assertEqual(gate['decision'], 'FAIL'); self.assertTrue(gate['blocking'])
        self.assertEqual(self.request('guard/runs/' + gate['id'], 'v')['decision'], 'FAIL')
        self.denied('guard/runs/' + gate['id'], 'b', code=404)
        self.denied('guard/runs', 'b', {'case_id': case_id, 'baseline_id': a, 'candidate_id': b}, code=400)
        self.denied('guard/cases', 'b', {'source_id': b, 'positive_id': a}, code=400)
        self.assertEqual(self.request('guard/usage')['imports'], 2)
        self.assertEqual(self.request('guard/usage', 'b')['imports'], 0)
        audit = (self.root / 'audit/alpha.jsonl').read_text()
        self.assertIn('/approve', audit); self.assertIn('guard/runs', audit)
        self.assertNotIn(CANARY, audit)
        for token in self.tokens.values(): self.assertNotIn(token, audit)

    def test_legacy_import_and_playback_share_quota(self):
        from agent_replay.guard.imports import normalize
        for version in ('A', 'B', 'C'):
            self.request('traces/import', body={'trace': normalize(example(version), 'generic')})
        self.denied('guard/import', 'a', {'trace': example('D'), 'format': 'generic'}, code=429)
        self.denied('traces/import', 'a', {'trace': normalize(example('D'), 'generic')}, code=429)
        self.denied('safe-replay', 'a', {'source_id': 'coding-A'}, code=429)
        self.assertEqual(self.request('guard/usage')['imports'], 3)

    def test_invalid_import_does_not_persist_raw_data(self):
        payload = example(); payload['started_at'] = CANARY
        self.denied('guard/import', 'a', {'trace': payload, 'format': 'generic'}, code=400)
        payload = example(); payload['output'] = 'x' * (1024 * 1024)
        self.denied('guard/import', 'a', {'trace': payload, 'format': 'generic'}, code=400)
        self.assertEqual(self.request('guard/usage')['imports'], 0)
        for path in self.root.rglob('*'):
            if path.is_file(): self.assertNotIn(CANARY, path.read_text())
