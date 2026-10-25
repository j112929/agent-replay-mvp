"""Multi-family leakage, held-out and provider-wire acceptance."""
import copy
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import tempfile
import threading
import unittest

from agent_replay.storage import read, save
from agent_replay.serialization import digest
from agent_replay.experience.collection import demo, export_collection, validate_collection, held_out, _inventory
from agent_replay.experience.export import assign_splits
from agent_replay.experience.models import run_models, evaluate_models, _validate_config
from agent_replay.experience.cli import main


class CollectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.runs = demo(cls.root / 'runs', family_count=3, budget=1)
        cls.dataset = cls.root / 'dataset'
        export_collection(cls.runs, cls.dataset)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def setUp(self):
        self.temp_case = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_case.cleanup)
        self.copy = Path(self.temp_case.name) / 'dataset'
        shutil.copytree(self.dataset, self.copy)

    def test_nonempty_splits_are_real_independent_qualified_executions(self):
        data = validate_collection(self.copy)
        self.assertEqual(data['manifest']['split_group_count'], 3)
        self.assertEqual(data['manifest']['record_count'], 9)
        self.assertTrue(all(data['manifest']['splits'].values()))
        owners = {}
        for record in data['records']:
            for token in [('family', record['family_id']), ('cluster', record['parent_cluster_id']), ('source', record['source_trace_digest'])] + [('failure', f) for f in record['parent_failure_ids']]:
                self.assertEqual(owners.setdefault(token, record['split']), record['split'])
            q = data['children'][record['child']]['objects'].get(record['qualification_digest'])
            self.assertEqual(q['state'], 'qualified')
            self.assertEqual(len(q['witnesses']), 8)

    def test_portable_moved_collection_and_heldout_denominators(self):
        report = held_out(self.copy)
        for split in ('validation', 'test'):
            self.assertEqual(report['splits'][split]['candidates']['fixed']['value'], 1)
            self.assertEqual(report['splits'][split]['candidates']['broken']['value'], 0)
            self.assertEqual(report['splits'][split]['candidates']['fixed']['denominator'], 1)
        self.assertEqual(report['rl_training_success_improvement']['state'], 'unmeasured')
        self.assertEqual(validate_collection(self.copy)['manifest'], validate_collection(self.dataset)['manifest'])

    def test_shared_source_and_equivalent_tasks_cannot_be_split_by_renaming(self):
        entries = [copy.deepcopy(run['bundle']['entries'][0]) for run in self.runs]
        for e in entries: e['_source_digests'] = ['shared-normalized-source']
        with self.assertRaisesRegex(ValueError, 'independent groups'):
            assign_splits(entries, require_nonempty=True)
        for e in entries: e.pop('_source_digests')
        entries[1]['requirement'] = copy.deepcopy(entries[0]['requirement'])
        entries[1]['environment'] = copy.deepcopy(entries[0]['environment'])
        assigned = assign_splits(entries)
        self.assertEqual(assigned[entries[0]['scenario']['id']], assigned[entries[1]['scenario']['id']])

    def test_forged_split_and_omitted_records_fail_even_with_new_checksums(self):
        path = self.copy / 'records.jsonl'
        rows = [json.loads(s) for s in path.read_text().splitlines()]
        rows[0]['split'] = 'test' if rows[0]['split'] != 'test' else 'train'
        path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        manifest = read(self.copy / 'manifest.json'); manifest['artifacts'] = _inventory(self.copy)
        save(self.copy / 'manifest.json', manifest)
        with self.assertRaises(ValueError): validate_collection(self.copy)

    def test_collection_rejects_symlinks_and_inventory_injection(self):
        (self.copy / 'extra').write_text('unlisted')
        with self.assertRaises(ValueError): validate_collection(self.copy)
        (self.copy / 'extra').unlink()
        (self.copy / 'link').symlink_to(self.dataset / 'manifest.json')
        with self.assertRaises(ValueError): validate_collection(self.copy)

    def test_duplicate_child_scenarios_and_immutable_destination_rejected(self):
        with self.assertRaises(ValueError): export_collection(self.runs, self.copy)
        with self.assertRaises(ValueError): export_collection([self.runs[0]] * 3, Path(self.temp_case.name) / 'duplicate')

    def test_model_config_rejects_fake_independence_and_unbounded_repetitions(self):
        model = {'model': 'x', 'version': '1', 'provider': {'kind': 'openai_compatible', 'base_url': 'https://example.com/v1'}}
        config = {'models': [model, copy.deepcopy(model)], 'scope': 'live', 'repetitions': 1}
        with self.assertRaises(ValueError): _validate_config(config)
        config['models'][1]['provider']['api_key_env'] = 'ANOTHER_KEY'
        with self.assertRaises(ValueError): _validate_config(config)
        config['models'][1]['model'] = 'y'; config['repetitions'] = 100
        with self.assertRaises(ValueError): _validate_config(config)
        self.assertEqual(main(['cross-model', str(self.copy), '--config', str(self.copy / 'manifest.json'), '--destination', str(Path(self.temp_case.name) / 'blocked')]), 2)

    def test_two_provider_wire_models_recomputed_but_excluded_from_live_metrics(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(request)
                public = json.loads(request['messages'][1]['content'])
                order = public['order']; task = public['task']
                amount = min(task['requested_minor'], order['approved_minor'], order['paid_minor'] - order['refunded_minor']) if request['model'] == 'wire-fixed' else order['paid_minor']
                payload = {'id': 'wire-response', 'model': request['model'], 'choices': [{'message': {'role': 'assistant', 'content': json.dumps({'order_id': order['order_id'], 'amount_minor': amount})}}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 10}}
                body = json.dumps(payload).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        provider = {'kind': 'openai_compatible', 'base_url': 'http://127.0.0.1:' + str(server.server_port)}
        config = {'scope': 'offline-wire-test', 'repetitions': 2, 'models': [{'model': m, 'version': 'test-v1', 'provider': provider} for m in ('wire-fixed', 'wire-broken')]}
        output = Path(self.temp_case.name) / 'models'
        value = run_models(self.copy, config, output)
        self.assertEqual(len(requests), 8)
        self.assertEqual(value['verdict_counts'], {'pass': 4, 'fail': 4, 'error': 0, 'inconclusive': 0})
        self.assertEqual(value['cross_model_failure_reproduction']['state'], 'unmeasured')
        self.assertEqual(evaluate_models(self.copy, output), value)
        for request in requests:
            self.assertNotIn('_environment', json.dumps(request))
            self.assertNotIn('witness_amount_minor', json.dumps(request))
            self.assertLessEqual(request.get('max_completion_tokens', request.get('max_tokens', 0)), 256)
        report = read(output / 'report.json')
        original = copy.deepcopy(report)
        report['runs'][0]['split'] = 'train'; save(output / 'report.json', report)
        with self.assertRaisesRegex(ValueError, 'train'): evaluate_models(self.copy, output)
        save(output / 'report.json', original)
        row = original['runs'][0]; artifact = output / row['experiment_path']
        experiment = read(artifact); pristine = copy.deepcopy(experiment)
        llm = next(s for s in experiment['trace']['steps'] if s['kind'] == 'llm')
        llm['replay_mode'] = 'fixture'
        save(artifact, experiment)
        report = copy.deepcopy(original); report['runs'][0]['experiment_digest'] = digest(experiment)
        save(output / 'report.json', report)
        self.assertEqual(evaluate_models(self.copy, output)['verdict_counts']['inconclusive'], 1)
        save(artifact, pristine); save(output / 'report.json', original)
        report = copy.deepcopy(original); report['runs'].pop(); save(output / 'report.json', report)
        with self.assertRaisesRegex(ValueError, 'omitted'): evaluate_models(self.copy, output)

    def test_provider_errors_do_not_count_as_refund_failures(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(503); self.end_headers(); self.wfile.write(b'unavailable')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        provider = {'kind': 'openai_compatible', 'base_url': 'http://127.0.0.1:' + str(server.server_port)}
        config = {'scope': 'offline-wire-test', 'repetitions': 1, 'models': [{'model': m, 'version': 'test', 'provider': provider} for m in ('unavailable-a', 'unavailable-b')]}
        result = run_models(self.copy, config, Path(self.temp_case.name) / 'unavailable')
        self.assertEqual(result['verdict_counts'], {'pass': 0, 'fail': 0, 'error': 4, 'inconclusive': 0})
        self.assertEqual(result['coverage']['decisive_runs'], 0)
        self.assertIsNone(result['held_out']['test'][0]['success_rate'])
        self.assertIsNone(result['cross_model_failure_reproduction']['value'])
