import tempfile
import unittest
from pathlib import Path
from agent_replay.benchmark.contracts import AgentConfig, BenchmarkTask, BenchmarkResult, BenchmarkRun
from agent_replay.benchmark.store import RunStore


class ContractsTests(unittest.TestCase):
    def test_contract_validation(self):
        for kwargs in ({'id': '../escape'}, {'timeout_seconds': -1}, {'tags': ['x', 'x']}):
            with self.assertRaises(ValueError):
                BenchmarkTask(**({'id': 'a', 'instruction': 'do it'} | kwargs))
        for kwargs in ({'success': 'yes'}, {'duration_ms': float('nan')}, {'steps': 1.5}, {'failure_category': 'other'}):
            with self.assertRaises(ValueError):
                BenchmarkResult(**({'task_id': 'a', 'success': False} | kwargs))

    def test_store_preserves_and_rejects_incomplete(self):
        with tempfile.TemporaryDirectory() as d:
            store = RunStore(Path(d) / 'run')
            run = BenchmarkRun('r', 'local', AgentConfig(), 'now', [{'id': 'a'}], 'digest')
            store.create(run)
            with self.assertRaises(FileExistsError):
                store.create(run)
            with self.assertRaises(ValueError):
                store.load()
            run.status = 'completed'
            store.persist(run, [BenchmarkResult('a', True)])
            self.assertTrue(store.load()[1][0].success)

import asyncio
import contextlib
from dataclasses import asdict
import io
import json
import os
import shutil
import subprocess
import sys
import time
from unittest.mock import patch
import xml.etree.ElementTree as ET
from agent_replay.benchmark.adapters.local import LocalAdapter, LocalEnvironment
from agent_replay.benchmark.agents.demo import DemoAgent
from agent_replay.benchmark.runner import run_benchmark
from agent_replay.benchmark.metrics import aggregate, distribution
from agent_replay.benchmark.compare import compare_runs, quality_gate
from agent_replay.benchmark.reports import write_reports
from agent_replay.capture import Capture
from agent_replay.cli import main
from agent_replay.contracts import validate_v2
from agent_replay.storage import read, save
from agent_replay.replay import replay_step

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / 'examples/benchmarks/terminal-smoke'


class BenchmarkStoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.baseline, cls.candidate = cls.root / 'baseline', cls.root / 'candidate'
        cls.a, cls.ar = run_benchmark(LocalAdapter(SUITE), DemoAgent(), AgentConfig(), cls.baseline)
        cls.b, cls.br = run_benchmark(LocalAdapter(SUITE), DemoAgent('demo-v2'), AgentConfig('demo-v2'), cls.candidate)
        cls.comparison = compare_runs(cls.baseline, cls.candidate)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_expected_outcomes_and_task_regressions(self):
        self.assertEqual(self.a.summary['pass_count'], 2)
        self.assertEqual(self.b.summary['pass_count'], 3)
        self.assertEqual(self.comparison['newly_passing'], ['finish-task', 'fix-answer'])
        self.assertEqual(self.comparison['newly_failing'], ['preserve-answer'])
        self.assertEqual(self.comparison['unchanged_pass'], ['create-file'])
        self.assertTrue(self.comparison['by_tag']['filesystem']['regression'])
        self.assertEqual(self.comparison['metrics']['success_rate']['delta'], .25)
        same = compare_runs(self.baseline, self.baseline)
        self.assertEqual(same['unchanged_fail'], ['finish-task', 'fix-answer'])

    def test_v2_capture_and_existing_failure_replay(self):
        result = next(r for r in self.ar if r.task_id == 'finish-task')
        self.assertEqual(result.failure_category, 'timeout')
        trace = validate_v2(read(self.baseline / result.trajectory_path))
        self.assertEqual(trace['metadata']['benchmark']['task_id'], result.task_id)
        self.assertEqual(trace['steps'][1]['parent_id'], trace['steps'][0]['id'])
        self.assertEqual(trace['evaluation']['verdict'], 'fail')
        replayed = replay_step(trace, trace['steps'][1]['id'])
        self.assertEqual(replayed['status'], 'error')
        from agent_replay.server import Store
        self.assertIn(trace['id'], Store(self.baseline, {}).traces())
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(['replay', str(self.baseline / result.trajectory_path), '--step', trace['steps'][1]['id'], '--directory', str(self.root / 'replayed')])
        self.assertEqual(code, 1)
        self.assertTrue(Path(json.loads(stdout.getvalue())['path']).is_file())
        try:
            import jsonschema
        except ImportError:
            self.skipTest('jsonschema dev dependency unavailable')
        jsonschema.validate(trace, read(ROOT / 'schema/trajectory-v2.schema.json'))

    def test_quality_gate_and_cli_exit_codes(self):
        self.assertFalse(quality_gate(self.comparison)['passed'])
        self.assertTrue(quality_gate(self.comparison, max_new_failures=1, max_cost_increase=0)['passed'])
        for limit, code in ((0, 1), (1, 0)):
            with contextlib.redirect_stdout(io.StringIO()):
                actual = main(['benchmark', 'gate', '--baseline', str(self.baseline), '--candidate', str(self.candidate), '--max-new-failures', str(limit), '--json'])
            self.assertEqual(actual, code)
        with self.assertRaises(ValueError):
            quality_gate(self.comparison, max_cost_increase=float('nan'))

    def test_reports_json_html_junit_and_links(self):
        reports = write_reports(self.candidate, comparison=self.comparison)
        self.assertEqual(read(reports['json'])['summary']['pass_count'], 3)
        suite = ET.parse(reports['junit']).getroot()
        self.assertEqual(suite.attrib['failures'], '1')
        self.assertEqual(suite.attrib['errors'], '0')
        self.assertIn('preserve-answer', Path(reports['html']).read_text())
        self.assertIn('../trajectories/', Path(reports['html']).read_text())

    def test_changed_and_missing_tasks_do_not_silently_pass(self):
        with tempfile.TemporaryDirectory() as d:
            copied = Path(d) / 'candidate'
            shutil.copytree(self.candidate, copied)
            run = read(copied / 'run.json')
            run['tasks'][0]['instruction'] = 'Different problem'
            save(copied / 'run.json', run)
            comparison = compare_runs(self.baseline, copied)
            self.assertEqual(comparison['changed_tasks'], ['create-file'])
            self.assertFalse(quality_gate(comparison, max_new_failures=10)['passed'])
            run['tasks'].pop()
            save(copied / 'run.json', run)
            rows = read(copied / 'results.json'); rows.pop(); save(copied / 'results.json', rows)
            comparison = compare_runs(self.baseline, copied)
            self.assertEqual(comparison['removed_tasks'], ['finish-task'])
            self.assertFalse(quality_gate(comparison, max_new_failures=10)['passed'])

    def test_cli_list_show_compare_report(self):
        for args in (['list', '--directory', str(self.root)], ['show', str(self.baseline)],
            ['compare', str(self.baseline), str(self.candidate)],
            ['report', str(self.candidate), '--baseline', str(self.baseline)]):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(['benchmark', *args, '--json']), 0)
                json.loads(out.getvalue())


class MetricsTests(unittest.TestCase):
    def test_missing_values_and_percentiles(self):
        self.assertEqual(distribution([1, 2, 3, 4, None]),
            {'count': 4, 'missing': 1, 'mean': 2.5, 'median': 2.5, 'p90': 4, 'p95': 4, 'total': 10})
        results = [BenchmarkResult('a', True, tokens=0, tags=['easy']),
                   BenchmarkResult('b', False, failure_category='tool_error', tags=['easy'])]
        summary = aggregate(results)
        self.assertEqual(summary['success_rate'], .5)
        self.assertEqual(summary['distributions']['tokens']['missing'], 1)
        self.assertIsNone(summary['distributions']['cost_usd']['mean'])
        self.assertEqual(summary['failure_categories'], {'tool_error': 1})
        self.assertIsNone(aggregate([])['success_rate'])


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.suite = self.root / 'suite'
        shutil.copytree(SUITE, self.suite)

    def tearDown(self):
        self.temp.cleanup()

    def run_task(self, actions, verifier=None, timeout=.3):
        tasks = read(self.suite / 'tasks.json')[:1]
        tasks[0]['metadata']['demo_actions']['demo'] = actions
        tasks[0]['timeout_seconds'] = timeout
        save(self.suite / 'tasks.json', tasks)
        if verifier is not None:
            (self.suite / tasks[0]['id'] / 'verifier.py').write_text(verifier)
        return run_benchmark(LocalAdapter(self.suite, verifier_timeout=.1), DemoAgent(), AgentConfig(), self.root / 'run')

    def test_tool_error(self):
        _, results = self.run_task([{'operation': 'execute', 'arguments': {'command': 'exit 127'}}])
        self.assertEqual(results[0].failure_category, 'tool_error')
        self.assertIn('127', results[0].metadata['error']['message'])

    def test_blocking_command_deadline(self):
        started = time.monotonic()
        _, results = self.run_task([{'operation': 'execute', 'arguments': {'command': 'sleep 10'}}], timeout=.1)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(results[0].failure_category, 'timeout')

    def test_verifier_exception_is_infrastructure_error(self):
        _, results = self.run_task([], 'raise RuntimeError("broken verifier")')
        self.assertEqual(results[0].failure_category, 'verifier_error')
        reports = write_reports(self.root / 'run')
        self.assertEqual(ET.parse(reports['junit']).getroot().attrib['errors'], '1')
        comparison = compare_runs(self.root / 'run', self.root / 'run')
        self.assertFalse(quality_gate(comparison)['passed'])

    def test_verifier_timeout(self):
        _, results = self.run_task([], 'import time; time.sleep(10)')
        self.assertEqual(results[0].failure_category, 'verifier_error')

    def test_malformed_verdict(self):
        _, results = self.run_task([], 'print(\'{"success": "false"}\')')
        self.assertEqual(results[0].failure_category, 'verifier_error')

    def test_path_escape_policy(self):
        _, results = self.run_task([{'operation': 'write_file', 'arguments': {'path': '../escape', 'content': 'bad'}}])
        self.assertEqual(results[0].failure_category, 'policy_violation')
        self.assertFalse((self.root / 'escape').exists())

    def test_file_actions_are_captured_and_workspace_cleaned(self):
        adapter = LocalAdapter(self.suite)
        task = adapter.load_tasks()[0]
        with Capture('tools', self.root / 'traces', input={}) as capture:
            env = adapter.prepare_environment(task, capture)
            env.write_file('/workspace/result.txt', 'hello agent')
            self.assertEqual(env.read_file('result.txt'), 'hello agent')
            self.assertEqual(env.list_files('.'), ['result.txt'])
            self.assertTrue(adapter.evaluate(task, {}, adapter.final_state(task, env)).success)
        adapter.cleanup()
        self.assertFalse(env.root.exists())
        self.assertEqual(len(read(capture.path)['steps']), 3)

    def test_duplicate_task_and_missing_verifier(self):
        tasks = read(self.suite / 'tasks.json')
        save(self.suite / 'tasks.json', tasks + tasks[:1])
        with self.assertRaises(ValueError):
            LocalAdapter(self.suite).load_tasks()
        save(self.suite / 'tasks.json', tasks)
        (self.suite / tasks[0]['id'] / 'verifier.py').unlink()
        with self.assertRaises(ValueError):
            LocalAdapter(self.suite).load_tasks()

    def test_existing_output_preserved_and_cli_run(self):
        output = self.root / 'cli'
        args = ['benchmark', 'run', '--suite', str(self.suite), '--agent', 'demo', '--output', str(output), '--json']
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(args), 0)
        self.assertEqual(json.loads(out.getvalue())['run']['summary']['pass_count'], 2)
        original = (output / 'run.json').read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(args), 2)
        self.assertEqual(original, (output / 'run.json').read_bytes())

    def test_model_agent_provider_reuse_and_unknown_cost(self):
        from agent_replay.benchmark.agents.model import OpenAICompatibleAgent
        config = AgentConfig('openai', 'test-model')
        usage = {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}
        responses = [
            {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': 'call-1', 'type': 'function', 'function': {'name': 'write_file',
                 'arguments': json.dumps({'path': 'result.txt', 'content': 'hello agent'})}}]}}], 'usage': usage},
            {'choices': [{'message': {'role': 'assistant', 'content': 'done'}}], 'usage': usage}]
        tasks = read(self.suite / 'tasks.json')[:1]; save(self.suite / 'tasks.json', tasks)
        with patch('agent_replay.capture.completion', side_effect=responses) as provider:
            _, results = run_benchmark(LocalAdapter(self.suite), OpenAICompatibleAgent(config), config, self.root / 'model')
        self.assertTrue(results[0].success)
        self.assertEqual(results[0].tokens, 30)
        self.assertEqual(results[0].model_call_count, 2)
        self.assertIsNone(results[0].cost_usd)
        self.assertEqual(provider.call_count, 2)
        comparison = compare_runs(self.root / 'model', self.root / 'model')
        self.assertFalse(quality_gate(comparison, max_cost_increase=.25)['passed'])


class GateBoundaryTests(unittest.TestCase):
    def comparison(self, before=.71, after=.69, cost_before=1, cost_after=1.25):
        return {'matched_count': 1, 'added_tasks': [], 'removed_tasks': [], 'changed_tasks': [],
            'newly_failing': [], 'infrastructure_errors': {'baseline': [], 'candidate': []},
            'metrics': {'success_rate': {'delta': after - before},
                        'cost_usd': {'mean': {'baseline': cost_before, 'candidate': cost_after,
                            'relative_change': cost_after / cost_before - 1 if cost_before else None}}},
            'baseline': {'distributions': {'cost_usd': {'count': 1, 'missing': 0}}},
            'candidate': {'distributions': {'cost_usd': {'count': 1, 'missing': 0}}}}

    def test_exact_thresholds_pass_exceeding_thresholds_fail(self):
        self.assertTrue(quality_gate(self.comparison(), .02, 0, .25)['passed'])
        self.assertFalse(quality_gate(self.comparison(after=.68), .02, 0, .25)['passed'])
        self.assertFalse(quality_gate(self.comparison(cost_after=1.26), .02, 0, .25)['passed'])
        self.assertFalse(quality_gate(self.comparison(cost_before=0, cost_after=1), .02, 0, .25)['passed'])

    def test_partial_cost_data_fails_closed(self):
        value = self.comparison()
        value['candidate']['distributions']['cost_usd']['missing'] = 1
        self.assertFalse(quality_gate(value, .02, 0, .25)['passed'])


class CustomAdapterTests(unittest.TestCase):
    def test_core_accepts_non_filesystem_environment(self):
        class Adapter:
            name = 'in-memory'
            cleaned = False
            def load_tasks(self):
                return [BenchmarkTask('sum', 'sum two values')]
            def prepare_environment(self, task, capture):
                return {'values': [2, 3]}
            def final_state(self, task, environment):
                return environment['answer']
            def evaluate(self, task, trajectory, final_state):
                return BenchmarkResult(task.id, final_state == 5)
            def cleanup(self):
                self.cleaned = True
        class Agent:
            async def run(self, task, environment, capture):
                with capture.span('sum', kind='tool', input=environment['values']) as step:
                    environment['answer'] = step.set_output(sum(environment['values']))
                return environment['answer']
        adapter = Adapter()
        with tempfile.TemporaryDirectory() as directory:
            _, results = run_benchmark(adapter, Agent(), AgentConfig('custom'), Path(directory) / 'run')
        self.assertTrue(results[0].success)
        self.assertTrue(adapter.cleaned)
