import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_replay.guard.imports import normalize, MAX_PAYLOAD
from agent_replay.guard.store import GuardStore, require_approval
from agent_replay.guard.store import QuotaExceeded
from agent_replay.guard.runner import run
from agent_replay.security.sanitizer import sanitize_artifact
from agent_replay.security.replay import playback
from agent_replay.storage import read, save
from agent_replay.regression.assertions import evaluate, validate_rules
from agent_replay.regression.runner import run_case


CANARY = 'sk-syntheticGuardToken012345678901234'


def example(version='B'):
    return {'id': 'coding-' + version, 'name': 'Repair addition', 'started_at': '2026-10-11T12:00:00Z',
        'status': 'completed', 'duration_ms': 4, 'input': {'task': 'Repair add(a,b)'},
        'output': {'fixed': version == 'A'},
        'metadata': {'agent_id': 'coding-demo', 'agent_version': version, 'authorization': 'Bearer ' + CANARY},
        'steps': [{'id': 'read-' + version, 'parent_id': None, 'kind': 'tool', 'name': 'repo.read',
                   'status': 'completed', 'start_ms': 0, 'duration_ms': 1,
                   'input': {'path': 'math.py'}, 'output': {'content': 'return a - b'}},
                  {'id': 'patch-' + version, 'parent_id': None, 'kind': 'tool', 'name': 'repo.patch',
                   'status': 'completed', 'start_ms': 2, 'duration_ms': 1,
                   'input': {'content': 'return a + b' if version == 'A' else 'return a * b'},
                   'output': {'simulated': True, 'applied': True}}],
        'failures': [] if version == 'A' else [{'category': 'wrong_answer', 'message': 'addition verifier rejected patch'}]}


def otlp_example():
    def attribute(key, value): return {'key': key, 'value': {'stringValue': value}}
    trace_id = 'a' * 32
    return {'resourceSpans': [{'resource': {'attributes': [attribute('service.name', 'coding-demo'), attribute('service.version', 'B'), attribute('Authorization', CANARY)]},
        'scopeSpans': [{'spans': [
            {'traceId': trace_id, 'spanId': 'b' * 16, 'name': 'coding task',
             'startTimeUnixNano': '1791710400000000000', 'endTimeUnixNano': '1791710400010000000',
             'status': {'code': 2}, 'attributes': [attribute('agent_replay.input', '{"task":"repair"}'), attribute('agent_replay.output', '{"ok":false}')]},
            {'traceId': trace_id, 'spanId': 'c' * 16, 'parentSpanId': 'b' * 16, 'name': 'read',
             'startTimeUnixNano': '1791710400001000000', 'endTimeUnixNano': '1791710400002000000',
             'attributes': [attribute('gen_ai.tool.name', 'repo.read'), attribute('agent_replay.input', '{"path":"math.py"}'), attribute('agent_replay.output', '{"content":"return a-b"}')]}]}]}]}


class ImportTests(unittest.TestCase):
    def test_generic_native_and_idempotent_sanitization(self):
        trace = normalize(example(), 'generic', 'alpha', 'default')
        self.assertNotIn(CANARY, json.dumps(trace))
        self.assertGreater(trace['privacy']['redacted_field_count'], 0)
        native = normalize(trace, 'native', 'beta')
        self.assertEqual(native['metadata']['guard']['org_id'], 'beta')
        self.assertEqual(native['privacy']['status'], 'sanitized')
        self.assertEqual(sanitize_artifact(native), sanitize_artifact(sanitize_artifact(native)))

    def test_otlp_preserves_relationship_and_redacts_attribute_keys(self):
        trace = normalize(otlp_example(), 'otlp')
        self.assertEqual(trace['steps'][1]['parent_id'], trace['steps'][0]['id'])
        self.assertEqual(trace['steps'][1]['input']['value'], {'path': 'math.py'})
        self.assertNotIn(CANARY, json.dumps(trace))
        self.assertEqual(trace['failures'][0]['category'], 'agent_error')

    def test_missing_otlp_payloads_remain_unknown(self):
        payload = otlp_example(); payload['resourceSpans'][0]['scopeSpans'][0]['spans'][1]['attributes'] = []
        trace = normalize(payload, 'otlp')
        self.assertFalse(trace['steps'][1]['input']['present'])
        self.assertEqual(trace['steps'][1]['output']['replayability'], 'unsupported')

    def test_invalid_payloads_fail_without_echoing_secrets(self):
        bad_parent = example(); bad_parent['steps'][0]['parent_id'] = 'missing'
        large = example(); large['output'] = 'x' * MAX_PAYLOAD
        bad_status = example(); bad_status['status'] = CANARY
        bad_timestamp = example(); bad_timestamp['started_at'] = CANARY
        unsupported = example(); unsupported['output'] = object()
        cyclic = example(); cyclic['input'] = cyclic
        for value in ([], bad_parent, large, bad_status, bad_timestamp, unsupported, cyclic):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(ValueError) as result: normalize(value, 'generic')
                self.assertNotIn(CANARY, str(result.exception))

    def test_custom_sensitive_fields_and_failed_sanitization(self):
        payload = example(); payload['metadata']['customer_record'] = 'private customer payload'
        self.assertNotIn('private customer payload', json.dumps(normalize(payload, 'generic', sensitive_fields=['customer_record'])))
        payload['output'] = '{bad token=' + CANARY
        with self.assertRaises(ValueError): normalize(payload, 'generic')

    def test_duplicate_and_partial_otlp_rejected(self):
        for change in ('mixed', 'parent', 'duplicate'):
            payload = otlp_example(); spans = payload['resourceSpans'][0]['scopeSpans'][0]['spans']
            if change == 'mixed': spans[1]['traceId'] = 'd' * 32
            elif change == 'parent': spans[1]['parentSpanId'] = 'd' * 16
            else: spans[1]['spanId'] = spans[0]['spanId']
            with self.assertRaises(ValueError): normalize(payload, 'otlp')


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.store = GuardStore(self.root)
        self.bad = self.store.import_trace(example(), 'generic')['id']
        self.good = self.store.import_trace(example('A'), 'generic')['id']

    def generate(self, **changes):
        return self.store.generate(self.bad, self.good, changes.get('expected', {'output': {'fixed': True}}), changes.get('case_id'))['case']

    def approve(self):
        case = self.generate(); self.store.approve(case['id'], 'synthetic-reviewer'); return case

    def test_generate_fixtures_validate_and_approve(self):
        case = self.generate()
        self.assertEqual(case['guard']['validation']['status'], 'validated')
        self.assertEqual(len(case['replay_spec']['fixtures']), 3)
        self.assertEqual(self.store.get_case(case['id'])['review']['state'], 'draft')
        review = self.store.approve(case['id'], 'joe')
        self.assertEqual(review['state'], 'approved')
        self.assertEqual(self.store.approve(case['id'], 'joe'), review)
        for path in self.root.rglob('*'):
            if path.is_file(): self.assertNotIn(CANARY, path.read_text())

    def test_ambiguous_or_missing_oracles_stay_incomplete(self):
        for expected in (None, {'output': {'fixed': False}}, {'assertions': [{'id': 'done', 'op': 'execution_completed'}]},
                         {'assertions': [{'id': 'schema', 'op': 'json_schema', 'path': '/output/value', 'expected': {'type': 'object'}}]}):
            case = self.store.generate(self.bad, self.good, expected)['case']
            self.assertEqual(case['guard']['validation']['status'], 'incomplete')
            with self.assertRaises(ValueError): self.store.approve(case['id'], 'joe')
        self.assertEqual(self.store.generate(self.bad)['case']['guard']['validation']['status'], 'incomplete')

    def test_redacted_or_missing_fixtures_block_generation(self):
        payload = example('C'); payload['steps'][0]['output'] = {'api_key': CANARY}
        ident = self.store.import_trace(payload, 'generic')['id']
        case = self.store.generate(ident, self.good, {'output': {'fixed': True}})['case']
        self.assertEqual(case['guard']['validation']['status'], 'incomplete')
        self.assertIn('recorded tool response', case['guard']['reason'])

    def test_approval_is_bound_to_version_rules_and_evidence(self):
        case = self.approve(); path = self.store.case_path(case['id'])
        changed = read(path); changed['assertions'][0]['expected'] = {'fixed': False}; save(path, changed)
        with self.assertRaises(ValueError): require_approval(changed, path, self.root)
        save(path, case)
        regenerated = self.generate(case_id=case['id'])
        self.assertEqual(regenerated['guard']['version'], 2)
        self.assertEqual(self.store.get_case(case['id'])['review']['state'], 'draft')
        positive = self.store.trace_path(self.good); data = read(positive); data['output']['value']['fixed'] = False; save(positive, data)
        with self.assertRaises(ValueError): self.store.approve(case['id'], 'joe')

    def test_safe_evidence_regression_and_gate(self):
        case = self.approve()
        with patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')), patch('subprocess.Popen', side_effect=AssertionError('process forbidden')):
            gate = run(self.store, case['id'], self.good, self.bad)
        self.assertEqual((gate['decision'], gate['classification'], gate['exit_code']), ('FAIL', 'regression', 1))
        self.assertEqual(gate['external_operations'], 0)
        self.assertEqual(gate['versions'], {'baseline': 'A', 'candidate': 'B'})
        result = self.root / 'guard/runs' / gate['id']
        self.assertTrue((result / 'reports/junit.xml').is_file())
        report = (result / 'reports/index.html').read_text()
        self.assertIn('CAPTURED_CANDIDATE_EVALUATION', report)
        self.assertIn('candidate', report)
        self.assertIn('Simulated recorded playback', report)
        self.assertTrue(read(result / 'playback.json')['replay']['simulated'])
        passed = run(self.store, case['id'], self.good, self.good)
        self.assertEqual(passed['decision'], 'PASS')

    def test_staged_report_over_storage_cap_is_removed(self):
        case = self.approve()
        store = GuardStore(self.root, limits={'storage_bytes': self.store.usage()['storage_bytes'] + 5 * 1024 * 1024})
        def oversized_report(results, directory):
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'index.html').write_text('<h1>Agent Regression Report</h1>')
            (directory / 'large.json').write_text('x' * (6 * 1024 * 1024))
        with patch('agent_replay.guard.runner.write_reports', side_effect=oversized_report):
            with self.assertRaises(QuotaExceeded): run(store, case['id'], self.good, self.bad)
        self.assertEqual(store.usage()['runs'], 0)
        self.assertFalse(list((self.root / 'guard/runs').glob('.pending-*')))

    def test_generated_artifacts_match_published_schemas(self):
        try: import jsonschema
        except ImportError: self.skipTest('Install .[dev] for schema validation')
        case = self.approve(); gate = run(self.store, case['id'], self.good, self.bad)
        schemas = Path(__file__).resolve().parents[1] / 'schema'
        for value, name in [(case, 'regression-case'), ({'schema_version': '1.0', 'fixtures': case['replay_spec']['fixtures']}, 'fixture-set')]:
            jsonschema.validate(value, read(schemas / (name + '.schema.json')))
        for result in gate['results']: jsonschema.validate(result, read(schemas / 'regression-result.schema.json'))
        jsonschema.validate(read(self.store.trace_path(self.bad)), read(schemas / 'trajectory-v2.schema.json'))

    def test_unknown_tool_missing_evidence_playback_and_wrong_task_block(self):
        case = self.approve()
        missing = run(self.store, case['id'], self.good)
        self.assertEqual((missing['decision'], missing['exit_code']), ('INCONCLUSIVE', 3))
        for modification in ('unknown_tool', 'input', 'identity', 'playback'):
            trace = read(self.store.trace_path(self.bad)); trace['id'] = 'changed-' + modification
            if modification == 'unknown_tool': trace['steps'][0]['name'] = 'payments.send'
            if modification == 'input': trace['input']['value'] = {'task': 'different'}
            if modification == 'identity': trace['metadata']['agent_id'] = 'unrelated-agent'
            if modification == 'playback': trace = playback(trace)
            ident = self.store.import_trace(trace)['id']
            result = run(self.store, case['id'], self.good, ident)
            self.assertEqual(result['decision'], 'INCONCLUSIVE')
            self.assertTrue(result['blocking'])

    def test_legacy_evaluator_cannot_bypass_approval(self):
        case = self.generate()
        result = run_case(self.store.case_path(case['id']), {'_root': str(self.root)}, self.root / 'legacy')
        self.assertEqual(result['verdict'], 'inconclusive')

    def test_quotas_duplicates_conflicts_and_paths(self):
        limits = {'imports': 2, 'cases': 1, 'runs': 1, 'storage_bytes': 64 * 1024 * 1024}
        store = GuardStore(self.root, limits=limits)
        self.assertTrue(store.import_trace(example(), 'generic')['duplicate'])
        with self.assertRaises(ValueError): store.import_trace(example('C'), 'generic')
        conflict = example(); conflict['output'] = {'fixed': True}
        with self.assertRaises(ValueError): store.import_trace(conflict, 'generic')
        case = store.generate(self.bad, self.good, {'output': {'fixed': True}})['case']; store.approve(case['id'], 'joe')
        with self.assertRaises(ValueError): store.generate(self.bad, self.good, {'output': {'fixed': True}})
        run(store, case['id'], self.good, self.bad)
        with self.assertRaises(ValueError): run(store, case['id'], self.good, self.bad)
        with self.assertRaises(ValueError): store.trace_path('../alpha')
        with self.assertRaises(ValueError): store.case_path('../../other')


class DeclarativeTests(unittest.TestCase):
    def test_schema_order_forbidden_and_predicates(self):
        trace = normalize(example('A'), 'generic')
        rules = [
            {'id': 'schema', 'op': 'json_schema', 'path': '/output/value', 'expected': {'type': 'object', 'required': ['fixed'], 'properties': {'fixed': {'type': 'boolean', 'const': True}}}},
            {'id': 'order', 'op': 'ordered', 'expected': [{'name': 'repo.read'}, {'name': 'repo.patch'}]},
            {'id': 'forbidden', 'op': 'forbidden', 'select': {'kind': 'tool', 'name': 'payments.send'}},
            {'id': 'predicate', 'op': 'predicate', 'path': '/output/value', 'expected': {'all': [{'op': 'exists', 'path': '/fixed'}, {'op': 'equals', 'path': '/fixed', 'value': True}]}}]
        validate_rules(rules); self.assertTrue(all(r['verdict'] == 'pass' for r in evaluate(trace, rules)))
        trace['steps'].reverse(); self.assertEqual(evaluate(trace, [rules[1]])[0]['verdict'], 'fail')
        trace = normalize(example('A'), 'generic'); trace['steps'][1]['execution']['start_ms'] = 0.5
        self.assertEqual(evaluate(trace, [rules[1]])[0]['verdict'], 'fail')
        for code in ({'$ref': 'https://production.example/schema'}, {'type': 'object', 'pattern': '.*'}, {'code': '__import__("os")'}):
            with self.assertRaises(ValueError): validate_rules([{'id': 'unsafe', 'op': 'json_schema', 'path': '/output/value', 'expected': code}])

    def test_boolean_numeric_and_missing_values_do_not_pass(self):
        trace = normalize(example('A'), 'generic')
        rules = [{'id': 'numeric', 'op': 'predicate', 'path': '/output/value', 'expected': {'op': 'gte', 'path': '/fixed', 'value': 1}},
                 {'id': 'missing', 'op': 'json_schema', 'path': '/output/value', 'expected': {'type': 'object', 'required': ['absent'], 'properties': {'absent': {'type': 'null'}}}}]
        validate_rules(rules); self.assertTrue(all(r['verdict'] == 'fail' for r in evaluate(trace, rules)))

    def test_nested_exact_values_and_invalid_selectors(self):
        trace = normalize(example('A'), 'generic')
        rules = [{'id': 'exact', 'op': 'equals', 'path': '/output/value', 'expected': {'fixed': 1}},
                 {'id': 'nested', 'op': 'predicate', 'path': '/output/value', 'expected': {'op': 'equals', 'path': '', 'value': {'fixed': 1}}},
                 {'id': 'contains', 'op': 'contains', 'path': '/output/value', 'expected': []}]
        validate_rules(rules); self.assertTrue(all(r['verdict'] == 'fail' for r in evaluate(trace, rules)))
        trace['output']['value'] = [True]
        self.assertEqual(evaluate(trace, [{'id': 'contains-bool', 'op': 'contains', 'path': '/output/value', 'expected': 1}])[0]['verdict'], 'fail')
        for selector in ({'kind': 'payment'}, {'scope_path': 'scope'}, {'name': []}, {'occurrence': True}):
            with self.assertRaises(ValueError): validate_rules([{'id': 'invalid', 'op': 'forbidden', 'select': selector}])
