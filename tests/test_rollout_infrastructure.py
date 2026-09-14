import json
from pathlib import Path
import tempfile
import unittest
from agent_replay.rollout.store import Store, Conflict
from agent_replay.rollout.worker import run_once
from agent_replay.rollout import bandit
from agent_replay.rollout.artifacts import LocalObjects, backup, restore, seal_checkpoint, verify_checkpoint
from agent_replay.rollout.metrics import benchmark_report, prometheus


class InfrastructureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root/'controller.db')
        self.store.initialize({'format':'bandit-softmax.v1','logits':[0.,0.]})

    def populate(self, n=4):
        self.store.enqueue(n,bandit.ENVIRONMENT,100,'group')
        for _ in range(n):
            run_once(self.store,'actor','a');run_once(self.store,'verifier','v')

    def test_object_integrity_and_controller_restart(self):
        self.populate(1)
        key = self.store.snapshot()['trajectories'][0]['id']
        expected = self.store.trajectory(key)
        self.assertEqual(Store(self.store.path).trajectory(key),expected)
        path = self.store.objects.path(expected['digest'])
        path.write_text('{}')
        with self.assertRaises(ValueError):self.store.trajectory(key)

    def test_backup_restore_revokes_live_leases(self):
        self.populate(1)
        self.store.enqueue(1,bandit.ENVIRONMENT,2,'pending')
        lease = self.store.claim('actor','old')
        backup(self.store,self.root/'backup')
        restore(self.root/'backup',self.root/'restored.db',self.root/'restored-objects')
        recovered = Store(self.root/'restored.db')
        self.assertEqual(recovered.snapshot()['total_trajectories'],1)
        with self.assertRaises(Conflict):recovered.heartbeat(lease['id'],lease['token'])
        self.assertEqual(recovered.claim('actor','new')['id'],lease['id'])
        with self.assertRaises(ValueError):restore(self.root/'backup',self.root/'restored.db',self.root/'restored-objects')

    def test_checkpoint_mutation_rejected(self):
        checkpoint = self.root/'weights';checkpoint.mkdir();(checkpoint/'model').write_bytes(b'weights')
        ref = seal_checkpoint(checkpoint)
        self.assertEqual(verify_checkpoint(ref,self.root),checkpoint.resolve())
        (checkpoint/'model').write_bytes(b'changed')
        with self.assertRaises(ValueError):verify_checkpoint(ref,self.root)

    def test_bounded_policy_lag(self):
        self.populate(8)
        self.store.batch(4,bandit.ENVIRONMENT,bandit.VERIFIER,'one')
        run_once(self.store,'learner','l')
        with self.assertRaises(Conflict):self.store.batch(4,bandit.ENVIRONMENT,bandit.VERIFIER,'strict')
        self.store.batch(4,bandit.ENVIRONMENT,bandit.VERIFIER,'lagged',max_policy_lag=1)
        job=self.store.claim('learner','l')
        self.assertEqual(job['payload']['policy_version'],1)
        self.assertEqual(job['payload']['behavior_policy_version'],0)
        with self.assertRaises(Conflict):self.store.batch(4,bandit.ENVIRONMENT,bandit.VERIFIER,'lagged',max_policy_lag=0)

    def test_measurement_dedup_and_denominators(self):
        self.store.measure('actor','idle_seconds',2,'id')
        self.store.measure('actor','idle_seconds',2,'id')
        self.store.measure('actor','busy_seconds',6,'busy')
        self.store.measure('actor','rollouts',8,'rollouts')
        self.store.measure('learner','training_step_seconds',3,'step')
        report=benchmark_report(self.store.metrics(),4,{})
        self.assertEqual(report['actor_idle']['fraction'],.25)
        self.assertEqual(report['rollout_throughput_per_second'],2)
        self.assertIsNone(report['tokens_per_second'])
        self.assertIsNone(report['gpu_utilization']['mean_percent'])
        self.assertIn('replay_training_step_seconds_bucket',prometheus(self.store.metrics()))
        with self.assertRaises(ValueError):self.store.measure('actor','idle_seconds',3,'id')

    def test_task_provenance_mismatch(self):
        self.store.enqueue(1,bandit.ENVIRONMENT,1,'task',task={'prompt':'a'})
        job=self.store.claim('actor','a')
        value=bandit.rollout(job['payload'],self.store.policy())
        with self.assertRaises(Conflict):self.store.complete(job['id'],job['token'],value)
