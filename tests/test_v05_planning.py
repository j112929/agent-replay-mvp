import asyncio
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest
from agent_replay.capture import Capture
from agent_replay.planning.core import Planner,PlanningAgent,CandidateGenerator
from agent_replay.planning.contracts import PlanningConfig
from agent_replay.world_model.refund import *

class ClosedLoopTests(unittest.TestCase):
    def run_agent(self,mode,correction=False,max_replans=2,index=1):
        task=tasks(index+1)[index];env=RefundEnvironment(task)
        config=PlanningConfig(enabled=True,mode=mode,horizon=3,rollouts_per_candidate=2,self_correction=correction,max_replans=max_replans)
        agent=PlanningAgent(RefundBaseline(),Planner(CandidateGenerator(policy,SCHEMAS),DeterministicRefundModel(),config))
        with tempfile.TemporaryDirectory() as root:
            with Capture('refund',root) as capture:result=asyncio.run(agent.run(task,env,capture))
            return result,env.calls,capture.trace
    def test_shadow_preserves_baseline_and_active_selects_valid_amount(self):
        shadow,calls,trace=self.run_agent('shadow')
        self.assertFalse(shadow['success']);self.assertNotIn('payments.refund',calls)
        self.assertEqual(trace['metadata']['planning']['sessions'][-1]['status'],'execution_denied')
        active,calls,trace=self.run_agent('active')
        self.assertTrue(active['success']);self.assertEqual(calls.count('payments.refund'),1)
    def test_correction_is_bounded_and_uses_actual_observation(self):
        result,calls,trace=self.run_agent('active',True,2,0)
        self.assertTrue(result['success']);self.assertEqual(calls.count('payments.refund'),1)
        self.assertEqual(trace['metadata']['planning']['correction_summary']['replans'],1)
        self.assertEqual(trace['metadata']['planning']['sessions'][0]['correction']['decision'],'replan')
        result,calls,trace=self.run_agent('active',True,0,0)
        self.assertFalse(result['success']);self.assertEqual(calls,['orders.lookup'])
        self.assertEqual(trace['metadata']['planning']['sessions'][0]['correction']['decision'],'stop')
    def test_new_defaults_do_not_change_legacy_feature_flag(self):
        self.assertFalse(PlanningConfig().enabled);self.assertEqual(PlanningConfig().mode,'active')
        for kw in ({'max_replans':-1},{'mode':'live'},{'max_uncertainty':2}):
            with self.assertRaises(ValueError):PlanningConfig(**kw)

class SafetyLimitTests(unittest.TestCase):
    def test_five_step_horizon_and_sample_variance_are_explicit(self):
        from agent_replay.planning.contracts import AgentContext,PredictionDistribution,ident
        class Nonterminal:
            model_id='nonterminal-fixture';version='v1'
            async def predict(self,ctx,a,config):
                return PredictionDistribution(ident(),a.candidate_id,[{'observation':{'status':'completed','state_delta':{}},'state_delta':{},'terminal':False,'success':None,'probability':1,'uncertainty':.2,'reward':0,'cost_usd':0}],self.model_id,self.version)
        ctx=AgentContext('task','family','wait',{'terminal':False},0,'fixture')
        g=CandidateGenerator(lambda c:[CandidateAction('wait','tool',{},'wait')],{'wait':{'type':'object','additionalProperties':False}})
        value=asyncio.run(Planner(g,Nonterminal(),PlanningConfig(enabled=True,horizon=5,rollouts_per_candidate=2)).plan(ctx))
        self.assertEqual(value['status'],'selected');self.assertTrue(all(len(r['simulated_steps'])==5 for r in value['rollouts']))
        self.assertEqual(value['selection']['alternative_candidates'][0]['score_variance'],0)
    def test_policy_violation_stops_even_when_model_predicted_it(self):
        class Violation(RefundEnvironment):
            def execute_action(self,a):
                out=super().execute_action(a);out.update(status='error',error_category='policy_violation');return out
        task=tasks(2)[1];env=Violation(task);config=PlanningConfig(enabled=True,self_correction=True)
        agent=PlanningAgent(RefundBaseline(),Planner(CandidateGenerator(policy,SCHEMAS),DeterministicRefundModel(),config))
        with tempfile.TemporaryDirectory() as root:
            with Capture('denied',root) as capture:asyncio.run(agent.run(task,env,capture))
            self.assertEqual(env.calls,['orders.lookup']);self.assertEqual(capture.trace['metadata']['planning']['sessions'][0]['correction']['reason'],'execution_policy_violation')
