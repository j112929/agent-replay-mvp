import concurrent.futures
import json
from pathlib import Path
import tempfile
import threading
import unittest

from agent_replay.rollout import bandit
from agent_replay.rollout.cli import export_trace
from agent_replay.rollout.http import Client, make_server
from agent_replay.rollout.store import Conflict, Store
from agent_replay.rollout.worker import run_once
from agent_replay.schema import validate_trace


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = [1000.0]
        self.store = Store(Path(self.tmp.name)/'store.db', clock=lambda: self.now[0])
        self.store.initialize({'format': 'bandit-softmax.v1', 'logits': [0., 0.]})

    def enqueue(self, count=1, key='test'):
        return self.store.enqueue(count, bandit.ENVIRONMENT, 100, key)

    def episode(self, job):
        return bandit.rollout(job['payload'], self.store.policy(job['payload']['policy_version']))

    def populate(self, count=8):
        self.enqueue(count)
        for _ in range(count):
            run_once(self.store, 'actor', 'actor')
            run_once(self.store, 'verifier', 'verifier')

    def test_concurrent_claims_are_exclusive_and_durable(self):
        self.enqueue(20)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            jobs = list(pool.map(lambda i: self.store.claim('actor', str(i)), range(20)))
        self.assertEqual(len({j['id'] for j in jobs}), 20)
        reopened = Store(self.store.path, clock=lambda: self.now[0])
        self.assertIsNone(reopened.claim('actor', 'new'))

    def test_fence_expiry_heartbeat_and_max_attempts(self):
        self.enqueue()
        first = self.store.claim('actor', 'one', 1)
        self.now[0] += 2
        with self.assertRaises(Conflict):
            self.store.complete(first['id'], first['token'], self.episode(first))
        second = self.store.claim('actor', 'two', 1)
        with self.assertRaises(Conflict):
            self.store.heartbeat(first['id'], first['token'])
        self.store.heartbeat(second['id'], second['token'], 10)
        self.now[0] += 2
        self.assertIsNone(self.store.claim('actor', 'three'))
        self.now[0] += 10
        third = self.store.claim('actor', 'three', 1)
        self.now[0] += 2
        self.assertIsNone(self.store.claim('actor', 'four'))
        self.assertEqual(self.store.snapshot()['jobs'][0]['state'], 'failed')
        self.assertNotEqual(second['token'], third['token'])

    def test_completion_idempotency_and_provenance(self):
        self.enqueue()
        job = self.store.claim('actor', 'one')
        value = self.episode(job)
        wrong = {**value, 'policy_digest': '0'*64}
        with self.assertRaises(Conflict):
            self.store.complete(job['id'], job['token'], wrong)
        first = self.store.complete(job['id'], job['token'], value)
        self.assertEqual(first, self.store.complete(job['id'], job['token'], value))
        with self.assertRaises(Conflict):
            self.store.complete(job['id'], job['token'], wrong)
        self.assertEqual(self.store.snapshot()['total_trajectories'], 1)
        with self.assertRaises(Conflict):
            self.store.batch(1, bandit.ENVIRONMENT, bandit.VERIFIER, 'too-early')

    def test_learning_and_publish_receipt(self):
        self.populate(16)
        self.store.batch(16, bandit.ENVIRONMENT, bandit.VERIFIER, 'batch')
        job = self.store.claim('learner', 'learner')
        samples = [self.store.trajectory(i) for i in job['payload']['trajectory_ids']]
        weights, metrics = bandit.learn(self.store.policy(), samples)
        result = self.store.publish(job['id'], job['token'], weights, metrics)
        self.assertEqual(result, self.store.publish(job['id'], job['token'], weights, metrics))
        self.assertEqual(result['policy_version'], 1)
        self.assertGreater(bandit.probabilities(weights)[1], .5)
        self.assertEqual(self.enqueue(16), [f'rollout:test:{i}' for i in range(16)])
        self.assertEqual(len(self.store.snapshot()['policies']), 2)
        with self.assertRaises(Conflict):
            self.store.enqueue(15, bandit.ENVIRONMENT, 100, 'test')
        with self.assertRaises(Conflict):
            self.store.batch(1, bandit.ENVIRONMENT, bandit.VERIFIER, 'reuse')

    def test_stale_learner_cannot_publish_and_batches_disjoint(self):
        self.populate(8)
        self.store.batch(4, bandit.ENVIRONMENT, bandit.VERIFIER, 'one')
        self.store.batch(4, bandit.ENVIRONMENT, bandit.VERIFIER, 'two')
        a = self.store.claim('learner', 'a')
        b = self.store.claim('learner', 'b')
        self.assertFalse(set(a['payload']['trajectory_ids']) & set(b['payload']['trajectory_ids']))
        self.store.publish(a['id'], a['token'], {'format': 'bandit-softmax.v1', 'logits': [-.1, .1]}, {})
        with self.assertRaises(Conflict):
            self.store.publish(b['id'], b['token'], {'format': 'bandit-softmax.v1', 'logits': [-.2, .2]}, {})

    def test_invalid_transition_and_reward_rejected(self):
        self.enqueue()
        job = self.store.claim('actor', 'actor')
        value = self.episode(job)
        value['transitions'][0]['behavior_logprob'] = float('nan')
        with self.assertRaises(ValueError):
            self.store.complete(job['id'], job['token'], value)
        run_once(self.store, 'verifier', 'empty')
        with self.assertRaises(ValueError):
            self.store.verify('missing', 'token', float('inf'), bandit.VERIFIER)

    def test_adapter_failure_retry_and_fence(self):
        self.enqueue()
        job = self.store.claim('actor', 'one')
        self.assertEqual(self.store.fail(job['id'], job['token'], 'actor', 'AdapterError')['state'], 'queued')
        with self.assertRaises(Conflict):
            self.store.complete(job['id'], job['token'], self.episode(job))
        next_job = self.store.claim('actor', 'two')
        self.assertEqual(next_job['attempts'], 2)
        self.assertEqual(self.store.fail(next_job['id'], next_job['token'], 'actor', 'BadConfig', False)['state'], 'failed')

    def test_episode_matches_public_schema(self):
        import jsonschema
        self.enqueue()
        job = self.store.claim('actor', 'one')
        schema = json.loads((Path(__file__).resolve().parents[1]/'schema'/'rollout-v1.json').read_text())
        jsonschema.validate(self.episode(job), schema)

    def test_deterministic_replay_and_debug_export(self):
        self.populate(2)
        row = self.store.trajectory(self.store.snapshot()['trajectories'][0]['id'])
        self.assertEqual(row['data'], bandit.rollout(row['data'], self.store.policy(0)))
        trace = export_trace(row)
        self.assertEqual(validate_trace(trace)['schema_version'], '2.0')


class HTTPTests(unittest.TestCase):
    def test_roles_and_full_worker_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'http.db')
            tokens = {r: r*30 for r in ('admin', 'actor', 'verifier', 'learner')}
            server = make_server(store, tokens, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f'http://127.0.0.1:{server.server_port}'
                admin = Client(url, tokens['admin'])
                actor = Client(url, tokens['actor'])
                admin.initialize(weights={'format': 'bandit-softmax.v1', 'logits': [0., 0.]})
                admin.enqueue(count=4, environment=bandit.ENVIRONMENT, seed=1, key='http')
                with self.assertRaises(ValueError):
                    actor.claim(kind='learner', owner='wrong')
                with self.assertRaises(ValueError):
                    actor.publish(job='x', token='x', weights={}, metrics={})
                with self.assertRaises(ValueError):
                    Client(url, 'invalid').snapshot()
                for _ in range(4):
                    run_once(actor, 'actor', 'actor')
                    run_once(Client(url, tokens['verifier']), 'verifier', 'verifier')
                admin.batch(size=4, environment=bandit.ENVIRONMENT, verifier=bandit.VERIFIER, key='batch')
                run_once(Client(url, tokens['learner']), 'learner', 'learner')
                self.assertEqual(admin.policy()['version'], 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_remote_plaintext_disallowed(self):
        with self.assertRaises(ValueError):
            Client('http://remote.example', 'token')
