import contextlib
import copy
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agent_replay.probabilistic.horizon.prediction_error import state_error, empirical_crps
from agent_replay.probabilistic.horizon.replanning_policy import ReplanningPolicy
from agent_replay.probabilistic.experiments.config import ExperimentConfig, from_dict
from agent_replay.probabilistic.experiments.runner import run_experiment
from agent_replay.probabilistic.experiments.reporting import public_artifact
from agent_replay.probabilistic.calibration.evaluator import evaluate_calibration
from agent_replay.benchmark.release import release_gate
from agent_replay.storage import read, save


class HorizonTests(unittest.TestCase):
    def test_numeric_errors_and_crps_not_text_rmse(self):
        error=state_error({'health':2.,'progress':3},{'health':1.,'progress':1})
        self.assertEqual(error['health']['squared_error'],1)
        self.assertEqual(error['progress']['squared_error'],4)
        self.assertEqual(state_error({'health':'healthy'},{'health':'bad'}),{})
        self.assertEqual(empirical_crps([0,2],1),.5)
        with self.assertRaises(ValueError):empirical_crps(['text'],1)

    def test_all_replanning_strategies_use_past_error_only(self):
        self.assertIsNone(ReplanningPolicy('open_loop').trigger(5,10,1))
        self.assertEqual(ReplanningPolicy('periodic',2).trigger(2,None,0),'periodic_interval')
        self.assertEqual(ReplanningPolicy('error_triggered').trigger(1,1,0),'observed_prediction_error')
        self.assertIsNone(ReplanningPolicy('error_triggered').trigger(1,None,1))
        self.assertEqual(ReplanningPolicy('uncertainty_triggered').trigger(1,0,.9),'predictive_uncertainty')

    def test_horizon_protocols_and_actual_replay_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=ExperimentConfig(tasks=2,seeds=[0],samples=2,candidate_count=2,horizons=[2],min_groups=2)
            result=run_experiment('horizon_reliability',Path(tmp)/'exp',cfg)
            self.assertEqual(len(result['runs']),4)
            for name,analysis in result['horizon_evaluations'].items():
                self.assertIn('teacher_forced_one_step',analysis['by_protocol'])
                self.assertTrue(all(h=='1' for h in analysis['by_protocol']['teacher_forced_one_step']))
                protocol='free_running_open_loop' if name.endswith('-open_loop') else 'closed_loop_observed_execution'
                self.assertIn(protocol,analysis['by_protocol'])
                detail=read(Path(tmp)/'exp'/name/'horizon.json')
                self.assertTrue(all(row['trajectory_id'] and row['step_id'] for row in detail['records']))
            public=public_artifact([result])
            self.assertNotIn('trajectory_id',str(public))
            self.assertTrue((Path(tmp)/'exp/index.html').exists())


class ExperimentTests(unittest.TestCase):
    def test_missing_world_model_support_stays_unlabeled(self):
        from agent_replay.probabilistic.experiments.runner import _run,compare_metric
        with tempfile.TemporaryDirectory() as tmp,patch('agent_replay.probabilistic.experiments.domain.WorkflowWorldModel.predict',side_effect=ValueError('unsupported environment')):
            from agent_replay.probabilistic.experiments.domain import make_tasks
            result,_=_run(Path(tmp),'unsupported',make_tasks(2,seeds=(0,)),ExperimentConfig(tasks=2,seeds=[0],samples=2))
            self.assertIsNone(result['summary']['success_rate'])
            self.assertEqual(result['summary']['label_coverage'],0)
            self.assertTrue(all(r['success'] is None for r in result['rows']))
            self.assertEqual(compare_metric(result['rows'],result['rows'],'success',0,2)['decision'],'INCONCLUSIVE')

    def test_gate_statistical_pass_fail_and_inconclusive(self):
        from agent_replay.probabilistic.gates import apply_gate
        root={'decision':'PASS','reasons':[],'policy':{'inconclusive_action':'block'}}
        a=[{'task_group':str(i),'trial_seed':s,'brier_score':.01} for i in range(24) for s in (0,1)]
        b=[{**r,'brier_score':.5} for r in a]
        evidence={'benchmark_version':'b1','evaluation_protocol':'paired-v1'}
        policy={'baseline_run_id':'baseline','min_groups':20,'min_labeled_samples':40,'max_brier_regression':.02}
        with patch('agent_replay.probabilistic.gates._load',side_effect=[({'id':'baseline'},evidence,a),({'id':'candidate'},evidence,b)]):
            gate=apply_gate(root,'a','b',policy)
            self.assertEqual(gate['decision'],'FAIL');self.assertTrue(gate['blocking'])
        with patch('agent_replay.probabilistic.gates._load',side_effect=[({'id':'baseline'},evidence,a),({'id':'candidate'},evidence,a)]):
            self.assertEqual(apply_gate(root,'a','b',policy)['decision'],'PASS')
        with patch('agent_replay.probabilistic.gates._load',side_effect=[({'id':'wrong'},evidence,a),({'id':'candidate'},evidence,a)]):
            self.assertEqual(apply_gate(root,'a','b',policy)['decision'],'INCONCLUSIVE')

    def test_experiment_budget_exhaustion_is_not_missing_label_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/'limited'
            cfg=ExperimentConfig(tasks=2,seeds=[0],candidate_count=2,samples=2,max_total_predictions=1)
            with self.assertRaises(ValueError):run_experiment('risk_planning',target,cfg)
            self.assertEqual(read(target/'experiment.json')['status'],'interrupted')
            results=read(target/'greedy/results.json')
            self.assertTrue(all(r['metadata']['probabilistic']['outcome']['success'] is None for r in results))

    def test_configuration_limits(self):
        for value in ({'tasks':10001},{'samples':33},{'seeds':[1,1]},{'horizons':[0]},{'provider':'arbitrary'},{'alpha':1}):
            with self.assertRaises(ValueError):from_dict(value)

    def test_risk_runner_existing_gate_inconclusive_and_leakage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'exp'
            cfg=ExperimentConfig(tasks=2,seeds=[0,1],samples=2,candidate_count=2,horizon=1,min_groups=2)
            result=run_experiment('risk_planning',root,cfg)
            self.assertEqual(len(result['runs']),5)
            policy={'environment':'DEVELOPMENT','min_tasks':2,'critical_tags':[],
                'probabilistic':{'baseline_run_id':result['runs']['expected_value']['run_id'],
                    'min_labeled_samples':200,'max_brier_regression':.02,'max_severe_failure_regression':.02}}
            gate=release_gate(root/'expected_value',root/'cvar',policy)
            self.assertEqual(gate['decision'],'INCONCLUSIVE')
            self.assertFalse(gate['blocking'])
            policy['environment']='PRODUCTION';policy['inconclusive_action']='block'
            self.assertTrue(release_gate(root/'expected_value',root/'cvar',policy)['blocking'])
            bad=copy.deepcopy(result);bad['synthetic']=False
            with self.assertRaises(ValueError):public_artifact([bad])
            result['secrets']={'api_key':'synthetic-private-canary'}
            result['runs']['customer-secret-name']={'summary':{'success_rate':1}}
            public=public_artifact([result])
            self.assertNotIn('synthetic-private-canary',str(public))
            self.assertNotIn('customer-secret-name',str(public))
            self.assertNotIn('trajectory_path',str(public))
            evidence=read(root/'cvar/results.json')
            evidence[0]['metadata']['probabilistic']['outcome']['source']='simulated'
            save(root/'cvar/results.json',evidence)
            self.assertEqual(release_gate(root/'expected_value',root/'cvar',policy)['decision'],'INCONCLUSIVE')

    def test_cli_smoke_and_safe_replay(self):
        from agent_replay.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.json'
            save(config,{'tasks':2,'seeds':[0],'samples':2,'candidate_count':2,'horizon':1,'min_groups':2})
            output=Path(tmp)/'run'
            with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['probabilistic','run','--experiment','risk_planning','--config',str(config),'--output',str(output)]),0)
                self.assertEqual(main(['probabilistic','replay',str(output/'cvar'),'risk-scenario-0-seed-0','--output',str(Path(tmp)/'replay.json')]),0)
                self.assertEqual(main(['probabilistic','publish',str(output/'experiment.json'),'--output',str(Path(tmp)/'public.json')]),0)
                self.assertEqual(main(['probabilistic','run','--experiment','calibration','--config',str(config),'--output',str(output)]),2)
            self.assertEqual(read(Path(tmp)/'replay.json')['replay']['external_operations'],0)


if __name__=='__main__':unittest.main()
