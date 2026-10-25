"""C/D acceptance: mutation, immutable compilation and verifier qualification."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from agent_replay.capture import capture
from agent_replay.demo import broken as source_broken
from agent_replay.storage import read, save
from agent_replay.serialization import digest
from agent_replay.experience.contracts import validate, SCHEMAS
from agent_replay.experience.pipeline import ingest, cluster, mutate
from agent_replay.experience.mutation import generate, validate_bundle, DEFAULT_REQUIREMENT, OPERATORS, identified
from agent_replay.experience.compiler import compile_bundle, execute_compiled, qualify, refund_loop, replay_spec
from agent_replay.experience.verifiers import evaluate_verifier, generate_verifier, qualification_current
from agent_replay.experience.stages import Stages
from agent_replay.experiments import resolve_spec
from agent_replay.project import load_project
from agent_replay.runtime.runner import run as replay_run
from agent_replay.runtime.policy import Policy
from agent_replay.replay import ReplayMismatch
from agent_replay.experience.refund import registry, reset, snapshot, cleanup

ROOT = Path(__file__).resolve().parents[1]


class RefundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        with capture('Refund policy', self.directory / 'seed', input={'order_id': 'o_1042'}, durability='sync') as run:
            run.set_output(source_broken(run))
            run.fail('Refund exceeds approval', category='assertion')
        self.source = read(run.path)
        self.dataset = ingest([self.source], directory=self.directory)['value']
        self.parent = cluster(self.dataset, directory=self.directory, config={'min_cluster_size': 1})['value']['clusters'][0]
        self.objects = Stages(self.directory).objects

    def bundle(self, operators=None, seed=0, budget=11):
        return mutate(self.dataset, self.parent, DEFAULT_REQUIREMENT, directory=self.directory,
                      operators=operators, seed=seed, budget=budget)['value']

    def test_operator_registry_seed_budget_dedup_and_immutability(self):
        before = copy.deepcopy((DEFAULT_REQUIREMENT, self.dataset, self.parent, self.source))
        bundle = self.bundle()
        again = self.bundle()
        self.assertEqual(bundle, again)
        self.assertEqual({x['scenario']['mutation']['operator_id'] for x in bundle['entries']}, set(OPERATORS))
        self.assertEqual(len({digest({'input': x['scenario']['input'], 'environment': x['environment']}) for x in bundle['entries']}), 11)
        self.assertEqual(len(self.bundle(budget=2)['entries']), 2)
        self.assertNotEqual(bundle['entries'], self.bundle(seed=37)['entries'])
        self.assertEqual(before, (DEFAULT_REQUIREMENT, self.dataset, self.parent, self.source))
        outcome = mutate(self.dataset, self.parent, DEFAULT_REQUIREMENT, directory=self.directory)
        self.assertTrue(outcome['reused'])

    def test_integer_boundaries_and_invariants(self):
        bundle = self.bundle(operators=['request_below', 'request_at', 'request_above'])
        self.assertEqual(sorted(x['scenario']['input']['requested_minor'] for x in bundle['entries']), [2999, 3000, 3001])
        for entry in bundle['entries']:
            initial = entry['environment']['initial_state']
            self.assertEqual(initial['witness_amount_minor'], min(3000, entry['scenario']['input']['requested_minor']))
            self.assertEqual(entry['verifier']['requirement_digests'], [digest(entry['requirement'])])
        bad = {**DEFAULT_REQUIREMENT, 'refunded_minor': 29900}
        with self.assertRaises(ValueError):
            generate(bad, self.parent, self.dataset)
        for budget in (0, 101, True):
            with self.assertRaises(ValueError):
                self.bundle(budget=budget)
        with self.assertRaises(ValueError):
            self.bundle(operators=['unknown'])
        with self.assertRaises(ValueError):
            self.bundle(operators=['wording', 'wording'])

    def test_artifact_tampering_rejected_before_execution(self):
        bundle = self.bundle(operators=['request_above'])
        for field in ('environment_digest', 'verifier_digest'):
            bad = copy.deepcopy(bundle)
            bad['entries'][0]['scenario'][field] = 'f' * 64
            with self.assertRaises(ValueError):
                compile_bundle(bad, self.source, self.directory)
        bad = copy.deepcopy(bundle)
        bad['entries'][0]['verifier']['assertions'] = [{'id': 'fake-success', 'op': 'execution_completed'}]
        with self.assertRaises(ValueError):
            validate_bundle(bad)
        unrelated = copy.deepcopy(self.source)
        unrelated['id'] = 'different-source'
        with self.assertRaises(ValueError):
            compile_bundle(bundle, unrelated, self.directory)

    def test_compiled_cases_reuse_regression_and_preserve_source(self):
        bundle = self.bundle(operators=['identifier_substitution'])
        entry = bundle['entries'][0]
        compiled = compile_bundle(bundle, self.source, self.directory)[0]
        original = copy.deepcopy(self.source)
        outcome = execute_compiled(compiled, entry, self.directory / 'candidate', self.objects)
        self.assertEqual(outcome['verdict'], 'pass')
        self.assertEqual(outcome['regression']['verdict'], 'pass')
        self.assertEqual(read(Path(compiled['case']).parent / 'source.json'), original)
        self.assertEqual(outcome['trace']['input']['value']['order_id'], entry['scenario']['input']['order_id'])
        self.assertEqual(outcome['trace']['replay']['source_digest'], digest(original))
        self.assertEqual(outcome['result']['usage']['model_calls'], 0)
        self.assertFalse(compiled['qualified'])
        save(compiled['case'], {**read(compiled['case']), 'entrypoint_id': 'broken'})
        with self.assertRaises(ValueError):
            compile_bundle(bundle, self.source, self.directory)

    def test_qualification_accepts_positive_and_rejects_seven_negatives(self):
        bundle = self.bundle(operators=['lookup_transient'])
        entry = bundle['entries'][0]
        reports = qualify(bundle, self.source, self.directory)
        report = reports[entry['scenario']['id']]
        self.assertEqual(report['state'], 'qualified')
        self.assertTrue(report['stable'])
        self.assertEqual(report['repeats'], 100)
        self.assertEqual(report['witnesses']['positive']['verdict'], 'pass')
        self.assertTrue(all(w['verdict'] == 'fail' for n, w in report['witnesses'].items() if n != 'positive'))
        self.assertTrue(qualification_current(report, entry, self.source, self.objects))
        compiled = compile_bundle(bundle, self.source, self.directory, qualifications=reports)
        self.assertTrue(compiled[0]['qualified'])
        with patch('agent_replay.experience.compiler.execute_compiled', side_effect=AssertionError('Cached qualification should not execute')):
            self.assertEqual(qualify(bundle, self.source, self.directory), reports)
        for field in ('requirement_digest', 'environment_digest', 'verifier_digest', 'implementation_digest', 'source_digest'):
            changed = copy.deepcopy(report)
            changed[field] = 'f' * 64
            with self.assertRaises(ValueError):
                qualification_current(changed, entry, self.source, self.objects)
        forged = copy.deepcopy(report)
        forged['witnesses']['misleading'] = copy.deepcopy(forged['witnesses']['positive'])
        with self.assertRaises(ValueError):
            qualification_current(forged, entry, self.source, self.objects)
        trace = self.objects.get(report['witnesses']['positive']['trace_digest'])
        missing = copy.deepcopy(trace)
        del missing['metadata']['environment_state']
        self.assertEqual(evaluate_verifier(missing, entry['verifier'], entry['environment'])['verdict'], 'inconclusive')
        incompatible = copy.deepcopy(trace)
        incompatible['metadata']['environment_state']['environment_digest'] = 'f' * 64
        self.assertEqual(evaluate_verifier(incompatible, entry['verifier'], entry['environment'])['verdict'], 'inconclusive')
        for _ in range(100):
            self.assertEqual(evaluate_verifier(trace, entry['verifier'], entry['environment'])['verdict'], 'pass')

    def test_draft_or_missing_qualification_cannot_be_promoted(self):
        bundle = self.bundle(operators=['request_at'])
        with self.assertRaises(ValueError):
            compile_bundle(bundle, self.source, self.directory, qualifications={})
        entry = bundle['entries'][0]
        with patch('agent_replay.experience.compiler.execute_compiled', return_value={'trace': None, 'verdict': 'error', 'reason': 'Worker unavailable', 'regression': {'verdict': 'error'}}):
            report = qualify(bundle, self.source, self.directory)[entry['scenario']['id']]
        self.assertEqual(report['state'], 'rejected')
        with self.assertRaises(ValueError):
            compile_bundle(bundle, self.source, self.directory, qualifications={entry['scenario']['id']: report})

    def test_fail_pass_fail_full_existing_suite_reports(self):
        report = refund_loop(self.bundle(operators=['request_above']), self.source, self.directory)
        self.assertEqual(report['exit_codes'], [1, 0, 1])
        self.assertEqual(report['external_calls'], 0)
        for name in ('before', 'after', 'regressed'):
            result = report['suites'][name]['results'][0]
            self.assertEqual(result['verifier_verdict'], result['verdict'])
            self.assertEqual(self.objects.get(result['scenario_result_digest'])['verdict'], result['verdict'])
            self.assertTrue(list((self.directory / name).rglob('junit.xml')))

    def test_state_reset_snapshot_cleanup_isolation_and_failure(self):
        entry = self.bundle(operators=['request_at'])['entries'][0]
        inputs = {**entry['scenario']['input'], '_environment': entry['environment']}
        a, b = reset(inputs), reset(inputs)
        a['orders']['o_1042']['refunded_minor'] = 1000
        self.assertEqual(b['orders']['o_1042']['refunded_minor'], 0)
        observed = snapshot(a)
        cleanup(a)
        self.assertEqual(a, {})
        self.assertEqual(observed['orders']['o_1042']['refunded_minor'], 1000)
        project = {**registry(), '_root': str(self.directory)}
        spec = resolve_spec({**replay_spec(entry), 'entrypoint_id': 'partial_execution'}, project)
        calls = []
        from agent_replay.project import callable_for as registered
        def selected(project, group, key):
            fn = registered(project, group, key)
            if key == 'refund-cleanup':
                return lambda state: (calls.append(copy.deepcopy(state)), fn(state))
            return fn
        with patch('agent_replay.runtime.runner.callable_for', side_effect=selected):
            trace = replay_run(self.source, spec, project, self.directory / 'failed')
        self.assertEqual(trace['execution']['status'], 'error')
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(trace['metadata']['environment_state']['ledger']), 1)

    def test_input_override_explicit_null_and_invalid_lifecycle(self):
        project = {**registry(), '_root': str(self.directory)}
        spec = resolve_spec({'entrypoint_id': 'fixed', 'input_override': None}, project)
        self.assertIsNone(spec['input_override'])
        with self.assertRaises(ValueError):
            resolve_spec({'entrypoint_id': 'fixed', 'input_override': float('nan')}, project)
        for d in ({'state_snapshot_adapter_id': 'refund-snapshot'}, {'state_adapter_id': 'unknown'}):
            with self.assertRaises(ValueError):
                resolve_spec({'entrypoint_id': 'fixed', 'determinism': d}, project)
        override = {'test': [1]}
        spec = resolve_spec({'entrypoint_id': 'fixed', 'input_override': override}, project)
        override['test'].append(2)
        self.assertEqual(spec['input_override'], {'test': [1]})

    def test_explicit_model_fixtures_consume_fail_closed_without_network(self):
        event = {'kind': 'llm', 'boundary_id': 'model', 'name': 'call', 'input': {'request': 'changed'}}
        fixture = {'id': 'offline', 'selector': {'kind': 'llm', 'boundary_id': 'model'},
                   'consume': 'once', 'expected_calls': 1,
                   'response': {'kind': 'return', 'value': {'choices': [{'message': {'content': 'ok'}}]}}}
        policy = Policy({'steps': []}, {'fixtures': [fixture], 'limits': {'max_model_calls': 0}}, {})
        policy.run = SimpleNamespace(_current_event=event, trace={'steps': [event]})
        self.assertEqual(policy.resolve_llm({})['choices'][0]['message']['content'], 'ok')
        self.assertEqual(policy.calls, 0)
        policy.fixtures.verify()
        with self.assertRaises(ReplayMismatch):
            policy.resolve_llm({})
        policy = Policy({'steps': []}, {}, {})
        policy.run = SimpleNamespace(_current_event=event, trace={'steps': [event]})
        with self.assertRaises(ReplayMismatch):
            policy.resolve_llm({})

    def test_model_fixture_capture_path_uses_changed_request_offline(self):
        response = {'choices': [{'message': {'role': 'assistant', 'content': 'fixture-answer'}}], 'usage': {'total_tokens': 3}}
        fixtures = [{'id': 'new-input-model', 'selector': {'name': 'chat.completions', 'kind': 'llm'},
                     'consume': 'once', 'expected_calls': 1, 'response': {'kind': 'return', 'value': response}}]
        policy = Policy({'steps': []}, {'fixtures': fixtures, 'limits': {'max_steps': 5, 'max_model_calls': 0}}, {})
        with patch('agent_replay.capture.completion', side_effect=AssertionError('Network must not be used')):
            with capture('Offline model fixture', self.directory / 'model', input={'changed': True}, durability='sync', _policy=policy) as run:
                policy.run = run
                result = run.chat(model='offline', messages=[{'role': 'user', 'content': 'mutated request'}])
                run.set_output(result['choices'][0]['message'])
                policy.fixtures.verify()
        trace = read(run.path)
        self.assertEqual(trace['steps'][0]['replay_mode'], 'fixture')
        self.assertEqual(trace['steps'][0]['response'], response)
        self.assertEqual(policy.calls, 0)

    def test_changed_input_without_simulator_fails_closed(self):
        entry = self.bundle(operators=['identifier_substitution'])['entries'][0]
        project = {**registry(), '_root': str(self.directory)}
        spec = replay_spec(entry)
        spec.pop('tool_rules')
        trace = replay_run(self.source, resolve_spec({**spec, 'entrypoint_id': 'fixed'}, project),
                           project, self.directory / 'unmatched')
        self.assertEqual(trace['execution']['status'], 'error')
        self.assertEqual(trace['execution']['error']['type'], 'ReplayMismatch')
        self.assertEqual(trace['metadata']['environment_state']['ledger'], [])
        self.assertEqual(evaluate_verifier(trace, entry['verifier'], entry['environment'])['verdict'], 'inconclusive')

    def test_small_policy_applies_only_satisfiable_operators(self):
        policy = {**DEFAULT_REQUIREMENT, 'approved_minor': 2, 'paid_minor': 2, 'requested_minor': 2}
        bundle = generate(policy, self.parent, self.dataset)
        self.assertEqual(len(bundle['entries']), 10)
        self.assertNotIn('request_below', {e['scenario']['mutation']['operator_id'] for e in bundle['entries']})
        with self.assertRaises(ValueError):
            generate(policy, self.parent, self.dataset, operators=['request_below'])

    def test_negative_worker_failure_does_not_qualify_a_verifier(self):
        bundle = self.bundle(operators=['request_at'])
        from agent_replay.experience.compiler import execute_compiled as real_execute
        def execute(item, entry, directory, objects):
            outcome = real_execute(item, entry, directory, objects)
            if read(item['case'])['entrypoint_id'] == 'wrong_entity':
                outcome['trace']['execution']['status'] = 'error'
                outcome['trace']['execution']['error'] = {'type': 'RuntimeError', 'message': 'Environment unavailable'}
                decision = evaluate_verifier(outcome['trace'], entry['verifier'], entry['environment'])
                outcome.update(decision)
            return outcome
        with patch('agent_replay.experience.compiler.execute_compiled', side_effect=execute):
            report = qualify(bundle, self.source, self.directory)[bundle['entries'][0]['scenario']['id']]
        self.assertEqual(report['state'], 'rejected')

    def test_corrupted_qualification_objects_are_not_reused(self):
        bundle = self.bundle(operators=['request_at'])
        report = qualify(bundle, self.source, self.directory)[bundle['entries'][0]['scenario']['id']]
        key = report['witnesses']['positive']['trace_digest']
        self.objects.path(key).write_text('{}')
        with self.assertRaises(ValueError):
            qualify(bundle, self.source, self.directory)

    def test_infrastructure_failure_is_not_mutated_as_refund(self):
        dataset = copy.deepcopy(self.dataset)
        record = dataset['records'][0]
        record['eligibility']['state'] = 'blocked'
        record['eligibility']['reasons'] = ['Incomplete capture']
        with self.assertRaises(ValueError):
            generate(DEFAULT_REQUIREMENT, self.parent, dataset)
        with self.assertRaises(ValueError):
            generate(DEFAULT_REQUIREMENT, self.parent, self.dataset, operators=[])

    def test_published_sidecars_match_runtime(self):
        import jsonschema
        bundle = self.bundle()
        jsonschema.validate(bundle, SCHEMAS['experience-scenario-set'])
        for entry in bundle['entries']:
            for key, name in [('scenario', 'experience-scenario'), ('environment', 'experience-environment'),
                              ('verifier', 'experience-verifier'), ('requirement', 'experience-refund-requirement')]:
                jsonschema.validate(entry[key], SCHEMAS[name])
                validate(entry[key], name)


if __name__ == '__main__':
    unittest.main()
