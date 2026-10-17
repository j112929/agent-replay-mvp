import asyncio
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest
from agent_replay.planning.metrics import calibration
from agent_replay.planning.dataset import extract, split, assert_disjoint
from agent_replay.planning.calibration import fit,transform,compare_versions,promote
from agent_replay.planning.store import PlanningStore
from agent_replay.planning.api import simulate
from agent_replay.planning.benchmark import tasks,DeploymentAdapter,agent
from agent_replay.planning.contracts import AgentContext
from agent_replay.planning.evaluation import evaluate_prediction
from agent_replay.planning.demo import demo,records_from_run
from agent_replay.benchmark.runner import run_benchmark
from agent_replay.benchmark.contracts import AgentConfig
from agent_replay.benchmark.compare import compare_runs
from agent_replay.benchmark.release import release_gate
from agent_replay.security.replay import playback
from agent_replay.storage import read


class DataTests(unittest.TestCase):
    def test_probability_metrics_ties_single_class_and_missing_data(self):
        rows=[{'probability':p,'outcome':y} for p,y in [(.1,False),(.8,True),(.4,False),(.7,True)]]
        m=calibration(rows);self.assertAlmostEqual(m['brier_score'],.075);self.assertEqual(m['roc_auc'],1)
        self.assertEqual(m['pr_auc'],1)
        ties=calibration([{'probability':.5,'outcome':True},{'probability':.5,'outcome':False}])
        self.assertEqual(ties['roc_auc'],.5);self.assertEqual(ties['pr_auc'],.5)
        self.assertIsNone(calibration([{'probability':1,'outcome':True}])['roc_auc'])
        self.assertIsNone(calibration([])['brier_score'])
        with self.assertRaises(ValueError):calibration([{'probability':.9,'outcome':.8}])

    def test_calibrator_requires_independent_training_and_uncertain_promotion_blocks(self):
        rows=[{'probability':.9,'outcome':i%2==0,'task_group':str(i)} for i in range(30)]
        model=fit(rows)
        self.assertAlmostEqual(transform(model,.9),.5)
        with self.assertRaises(ValueError):fit(rows[:3])
        with self.assertRaises(ValueError):fit(rows+[rows[0]])
        self.assertEqual(compare_versions(rows[:4],rows[:4])['decision'],'INCONCLUSIVE')
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):promote(model,[],[],[],rows,rows,Path(root)/'promotion.json')

    def test_held_out_calibration_promotion_and_overlap_rejection(self):
        def records(prefix,count):
            return [{'schema_version':'world-transition.v1','origin':'real_tool_observation','task_group':f'{prefix}-{i}',
                'source_trajectory_id':f'{prefix}-{i}','source_step_id':str(i),'history_context':{'unique_state':f'{prefix}-{i}'},
                'action':{'tool_name':'deploy','action_payload':{}},'actual_observation':{'success':i%2==0},
                'actual_state_delta':{},'environment_version':'v1','timestamp':'2026-10-17T00:00:00Z'} for i in range(count)]
        train,validation,test=records('train',40),records('validation',10),records('test',40)
        forecasts=lambda rs:[{'task_group':r['task_group'],'probability':1.,'outcome':r['actual_observation']['success']} for r in rs]
        model=fit(forecasts(train));before=forecasts(test);after=[{**r,'probability':transform(model,r['probability'])} for r in before]
        with tempfile.TemporaryDirectory() as root:
            result=promote(model,train,validation,test,before,after,Path(root)/'promoted.json')
            self.assertEqual(result['evaluation']['decision'],'IMPROVED');self.assertFalse(result['online_weight_updates'])
            with self.assertRaises(ValueError):promote(model,train,validation,train,forecasts(train),after,Path(root)/'bad.json')

    def test_simulation_session_schema_and_backward_replay(self):
        import jsonschema
        ctx=AgentContext('task','group','Deploy',{'tests_run':False,'compat_checked':False,'remaining_steps':6},0,'deployment-memory.v1')
        with tempfile.TemporaryDirectory() as root:
            session=simulate({'context':asdict(ctx),'config':{'enabled':True}},root)
            jsonschema.validate(session,read(Path(__file__).resolve().parents[1]/'schema/planning-session.schema.json'))
            store=PlanningStore(root);self.assertEqual(store.get(session['session_id']),session)
            self.assertIsNone(session['real_execution'])
            with self.assertRaises(ValueError):simulate({'context':asdict(ctx),'config':{}},root)
            with self.assertRaises(ValueError):simulate({'context':asdict(ctx),'config':{'enabled':True},'provider_url':'https://production.invalid'},root)

    def test_paired_benchmarks_replay_real_dataset_and_fail_closed_gate(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'experiment';result=demo(path,8)
            self.assertEqual(result['modes']['baseline']['passed'],2)
            self.assertEqual(result['tasks_per_mode'],8)
            self.assertEqual(result['quality_gate']['decision'],'INCONCLUSIVE')
            self.assertEqual(result['real_external_operations'],0)
            rows=records_from_run(path/'planning')
            self.assertTrue(rows);self.assertTrue(all(r['origin']=='real_tool_observation' for r in rows))
            assert_disjoint(records_from_run(path/'collection'),rows)
            with self.assertRaises(ValueError):assert_disjoint(rows,rows)
            parts=split(rows);self.assertEqual(sum(map(len,parts)),len(rows))
            trace=read(next((path/'planning/trajectories').glob('*.json')))
            replay=playback(trace)
            self.assertEqual(replay['metadata']['planning'],trace['metadata']['planning'])
            self.assertEqual(extract(replay),[])
            comparison=compare_runs(path/'baseline',path/'planning')
            self.assertIn('planning',comparison['metrics'])
            gate=release_gate(path/'baseline',path/'planning',{'min_tasks':2,'critical_tags':[],'planning':{'max_total_cost_increase':0}})
            self.assertEqual(gate['decision'],'FAIL')
            from agent_replay.benchmark.cli import main
            from agent_replay.planning.cli import main as planning_main
            from agent_replay.storage import save
            import contextlib,io,json
            policy=path/'policy.json';save(policy,{'min_tasks':2,'critical_tags':[],'planning':{'max_total_cost_increase':0}})
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['compare',str(path/'baseline'),str(path/'planning'),'--json']),0)
            self.assertIn('planning',json.loads(output.getvalue())['metrics'])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['gate','--baseline',str(path/'baseline'),'--candidate',str(path/'planning'),'--policy',str(policy)]),1)
                self.assertEqual(planning_main(['dataset',str(path/'planning'),'--output',str(path/'cli-data.json')]),0)
                self.assertEqual(main(['run','--benchmark','predictive-deploy','--mode','planning','--tasks','1','--output',str(path/'cli-run')]),0)
            self.assertTrue(read(path/'cli-data.json'))


if __name__=='__main__':unittest.main()
