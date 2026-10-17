import asyncio
from dataclasses import asdict
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from agent_replay.capture import Capture
from agent_replay.storage import read
from agent_replay.benchmark.runner import run_benchmark
from agent_replay.benchmark.contracts import AgentConfig
from agent_replay.world_model.refund import RefundAdapter,RefundBaseline,RefundEnvironment,DeterministicRefundModel,tasks,policy,action,ENVIRONMENT,SCHEMAS
from agent_replay.world_model.dataset import build,load,transitions
from agent_replay.world_model.evaluation import evaluate
from agent_replay.world_model.interfaces import WorldModelAdapter,PredictionRequest
from agent_replay.planning.contracts import AgentContext,PredictionConfig

class WorldModelTests(unittest.TestCase):
    def test_refund_oracle_matches_actual_doubles_without_executing_during_prediction(self):
        task=tasks(2)[1];env=RefundEnvironment(task);model=DeterministicRefundModel()
        for name,args in [('orders.lookup',{}),('approvals.check',{}),('payments.refund',{'amount_minor':3000})]:
            snap=env.snapshot();c=AgentContext(task.id,'g',task.instruction,snap['state'],snap['version'],ENVIRONMENT)
            a=action(name,args)
            with patch.object(env,'execute_action',side_effect=AssertionError('prediction executed')):
                p=asyncio.run(model.predict(c,a,PredictionConfig()))
            actual=env.execute_action(a)
            self.assertEqual(p.outcomes[0]['observation'],actual)
        self.assertTrue(env.state['success'])
        self.assertFalse(env.authorize(action('payments.refund',{'amount_minor':3000}),env.version))

    def test_dataset_and_evaluation_grouped_reproducible_authorized(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);run_benchmark(RefundAdapter(tasks(24)),RefundBaseline(),AgentConfig('refund'),root/'run')
            traces=[read(p) for p in (root/'run/trajectories').glob('*.json')]
            with self.assertRaises(ValueError):build(traces,root/'unauthorized')
            one=build(traces,root/'data',authorized=True);two=build(traces,root/'data2',authorized=True)
            self.assertEqual(one,two)
            manifest,rows=load(root/'data','test');self.assertTrue(rows)
            with patch('urllib.request.urlopen',side_effect=AssertionError('no network')):
                value=asyncio.run(evaluate(DeterministicRefundModel(),rows,root/'evaluation.json'))
            self.assertEqual(value['summary']['schema_validity'],1)
            self.assertLessEqual(value['summary']['exact_match'],1)
            self.assertTrue(all(r['trajectory_id'] and r['step_id'] for r in value['errors']))
            (root/'data/test.jsonl').write_text('{}\n')
            with self.assertRaises(ValueError):load(root/'data','test')

    def test_legacy_refund_converter_is_explicit_and_keeps_actual_outputs(self):
        from agent_replay.demo import demo
        with tempfile.TemporaryDirectory() as d:
            demo(d);trace=read(next((Path(d)/'traces').glob('*.json')))
            self.assertEqual(transitions(trace),[])
            rows=transitions(trace,'legacy-refund')
            self.assertEqual(len(rows),2);self.assertEqual(rows[-1]['actual_observation']['amount_minor'],29900)
            self.assertEqual(rows[0]['provenance']['source'],'real_execution')
            from agent_replay.migrations import legacy
            self.assertEqual(transitions(legacy(trace),'legacy-refund')[-1]['actual_observation'],rows[-1]['actual_observation'])
            self.assertFalse(rows[0]['state_delta_available'])
            import copy
            private=copy.deepcopy(trace);private['steps'][-1]['output']['value']['customer_email']='synthetic@example.invalid'
            self.assertEqual(len(transitions(private,'legacy-refund')),1)

    def test_batch_interface_and_environment_mismatch(self):
        snap=RefundEnvironment(tasks(1)[0]).snapshot();c=AgentContext('t','g','Refund',snap['state'],0,ENVIRONMENT)
        wrapper=WorldModelAdapter(DeterministicRefundModel());a=action('orders.lookup')
        self.assertEqual(len(asyncio.run(wrapper.predict_batch([PredictionRequest(c,a)]))),1)
        with self.assertRaises(ValueError):asyncio.run(wrapper.predict_batch([PredictionRequest(c,a)]*33))
        c.environment_version='other'
        with self.assertRaises(ValueError):asyncio.run(wrapper.predict_next([],a,c))

if __name__=='__main__':unittest.main()

class TrainingTests(unittest.TestCase):
    def test_sampled_policy_cannot_mutate_context_or_drop_duplicate_generation_cost(self):
        from agent_replay.world_model.providers import SampledCandidateGenerator
        from agent_replay.planning.core import Planner
        from agent_replay.planning.contracts import PlanningConfig
        snap=RefundEnvironment(tasks(1)[0]).snapshot();context=AgentContext('t','g','Refund',snap['state'],0,ENVIRONMENT)
        original=asdict(context)
        async def sampler(copy,seed):
            copy.state['requested_minor']=99999
            candidate=action('orders.lookup');candidate.generation_cost=.1;return candidate
        generator=SampledCandidateGenerator(sampler,SCHEMAS)
        result=asyncio.run(Planner(generator,DeterministicRefundModel(),PlanningConfig(enabled=True,horizon=1,rollouts_per_candidate=1)).plan(context))
        self.assertEqual(asdict(context),original);self.assertEqual(result['status'],'selected')
        self.assertAlmostEqual(result['budget']['cost_usd'],.6)

    def test_cpu_supervised_checkpoint_evaluation_and_integrity(self):
        from agent_replay.world_model.training import train,TabularWorldModel
        from agent_replay.world_model.registry import promote
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);run_benchmark(RefundAdapter(tasks(24)),RefundBaseline(),AgentConfig('refund'),root/'run')
            build([read(p) for p in (root/'run/trajectories').glob('*.json')],root/'dataset',authorized=True)
            result=train(root/'dataset',root/'checkpoints/cpu');model=TabularWorldModel(result['checkpoint'],root/'checkpoints')
            _,rows=load(root/'dataset','test');value=asyncio.run(evaluate(model,rows))
            self.assertGreater(value['summary']['count'],0);self.assertGreaterEqual(result['metrics']['negative_log_likelihood'],0)
            self.assertEqual(model.value['training_digest'],read(root/'checkpoints/cpu/model.json')['training_digest'])
            with self.assertRaises(ValueError):promote(result['checkpoint'],root/'checkpoints',value,root/'missing',{},root/'registry')
            (root/'checkpoints/cpu/model.json').write_text('{}')
            with self.assertRaises(ValueError):TabularWorldModel(result['checkpoint'],root/'checkpoints')

    def test_sampled_generation_retry_and_no_fake_calibration(self):
        from agent_replay.world_model.providers import SampledCandidateGenerator,RetryingWorldModel
        snap=RefundEnvironment(tasks(1)[0]).snapshot();c=AgentContext('t','g','Refund',snap['state'],0,ENVIRONMENT)
        async def sampler(ctx,seed):return action('orders.lookup')
        self.assertEqual(len(asyncio.run(SampledCandidateGenerator(sampler,{'orders.lookup':SCHEMAS['orders.lookup']}).generate(c,3))),1)
        class Flaky(DeterministicRefundModel):
            calls=0
            async def predict(self,*args):
                self.calls+=1
                if self.calls==1:raise ValueError('invalid structured response')
                return await super().predict(*args)
        result=asyncio.run(RetryingWorldModel(Flaky()).predict(c,action('orders.lookup'),PredictionConfig()))
        self.assertEqual(result.metadata['prediction_retries'],1);self.assertIsNone(result.cost_usd)
        self.assertEqual(result.calibration_status,'uncalibrated')

class PromotionTests(unittest.TestCase):
    def test_heldout_quality_and_real_shadow_gate_before_registry_promotion(self):
        from agent_replay.world_model.training import train,TabularWorldModel
        from agent_replay.world_model.registry import promote,rollback
        from agent_replay.planning.contracts import PlanningConfig
        from agent_replay.planning.core import Planner,PlanningAgent,CandidateGenerator
        from agent_replay.rollout.artifacts import LocalObjects
        from agent_replay.serialization import digest
        def clean(prefix,count):
            values=tasks(count,prefix)
            for task in values:task.metadata.update(requested_minor=2900,context_drift=False)
            return values
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);run_benchmark(RefundAdapter(clean('collect',24)),RefundBaseline(),AgentConfig('collect'),root/'run')
            build([read(p) for p in (root/'run/trajectories').glob('*.json')],root/'data',authorized=True)
            fitted=train(root/'data',root/'models/cpu');model=TabularWorldModel(fitted['checkpoint'],root/'models');_,rows=load(root/'data','test')
            evaluation=asyncio.run(evaluate(model,rows))
            config=PlanningConfig(enabled=True,mode='shadow',horizon=1,rollouts_per_candidate=1)
            generator=CandidateGenerator(lambda c:[RefundBaseline().choose_action(c)],SCHEMAS)
            agent=PlanningAgent(RefundBaseline(),Planner(generator,model,config))
            run_benchmark(RefundAdapter(clean('shadow',8)),agent,AgentConfig('shadow',parameters={'planning_mode':'shadow'}),root/'shadow')
            thresholds={'min_exact_match':1,'max_prediction_failure_rate':0,'max_latency_ms':1000,'max_cost_per_prediction':0,'min_heldout_groups':2,'min_shadow_tasks':8,'max_exact_match_drop':0,'max_brier_increase':0}
            result=promote(fitted['checkpoint'],root/'models',evaluation,root/'shadow',thresholds,root/'registry',asyncio.run(evaluate(DeterministicRefundModel(),rows)))
            self.assertEqual(result['stage'],'active_eligible');self.assertEqual(result['deployment'],'offline_reference_only')
            self.assertEqual(rollback(root/'registry',digest(result)),result)
            evaluation['task_groups']=model.value['training_groups']
            with self.assertRaises(ValueError):promote(fitted['checkpoint'],root/'models',evaluation,root/'shadow',thresholds,root/'registry',asyncio.run(evaluate(DeterministicRefundModel(),rows)))

class PublicArtifactTests(unittest.TestCase):
    def test_public_export_cannot_copy_raw_private_extension_fields(self):
        from agent_replay.world_model.publish import public_report
        sample={'schema_version':'world-model-experiment.v1','synthetic':True,'task_count':1,'modes':{'baseline':{'task_count':1,'success_rate':1,'pass_count':1,'fail_count':0,
            'distributions':{k:{'mean':1,'private':'synthetic confidential detail'} for k in ('duration_ms','cost_usd','tool_call_count')},'planning':{'raw_trace':'synthetic confidential detail'}}},'evaluations':{},'quality_gate':{'decision':'FAIL','reasons':['synthetic confidential detail']},'limitations':['synthetic confidential detail']}
        self.assertNotIn('synthetic confidential detail',str(public_report(sample)))
        sample['synthetic']=False
        with self.assertRaises(ValueError):public_report(sample)
