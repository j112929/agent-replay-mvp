import copy
from datetime import datetime, timezone, timedelta
import math
import unittest
from agent_replay.probabilistic.calibration.schemas import SuccessForecast, VerifiedOutcome
from agent_replay.probabilistic.calibration.collector import join_outcomes
from agent_replay.probabilistic.calibration.metrics import evaluate_metrics
from agent_replay.probabilistic.calibration.calibrators import fit, transform, logistic_fit
from agent_replay.probabilistic.experiments.statistics import paired_effect, holm


def forecast(**kwargs):
    t = datetime(2026,1,1,tzinfo=timezone.utc)
    value = dict(forecast_id='f1',run_id='run1',task_id='task1',trajectory_id='trace1',task_group='scenario1',
        policy_version='p1',model_version='m1',benchmark_version='b1',environment_version='e1',tool_version='t1',
        probability=.8,forecast_timestamp=t.isoformat(),information_cutoff=(t-timedelta(seconds=1)).isoformat())
    return SuccessForecast(**{**value,**kwargs})


def outcome(**kwargs):
    value = dict(run_id='run1',task_id='task1',trajectory_id='trace1',task_group='scenario1',success=True,
        benchmark_version='b1',environment_version='e1',tool_version='t1',outcome_timestamp='2026-01-01T00:00:01+00:00')
    return VerifiedOutcome(**{**value,**kwargs})


def rows(split='train', prefix='s'):
    return [dict(probability=p,outcome=y,task_group=f'{prefix}{i}',trial_seed=0,split=split,
                 provenance={'scenario_fingerprint':f'{prefix}{i}'})
            for i,(p,y) in enumerate([(.1,False),(.1,True),(.4,False),(.4,True),(.8,True),(.8,False)]*5)]


class CalibrationTests(unittest.TestCase):
    def test_temporal_join_versions_and_missing(self):
        joined = join_outcomes([forecast()], [outcome()])
        self.assertTrue(joined['rows'][0]['outcome'])
        self.assertEqual(join_outcomes([forecast()],[])['unlabeled'][0]['label_status'],'missing')
        self.assertEqual(join_outcomes([forecast()],[outcome(status='censored',success=None)])['rows'],[])
        for invalid in [outcome(environment_version='e2'),outcome(trajectory_id='other'),outcome(outcome_timestamp='2025-01-01T00:00:00+00:00')]:
            with self.assertRaises(ValueError): join_outcomes([forecast()],[invalid])

    def test_no_duplicate_simulated_labels_or_mixed_scope(self):
        with self.assertRaises(ValueError): join_outcomes([forecast(),forecast(forecast_id='f2')],[outcome()])
        with self.assertRaises(ValueError): join_outcomes([forecast()],[outcome(),outcome()])
        with self.assertRaises(ValueError): outcome(source='simulated')
        with self.assertRaises(ValueError): join_outcomes([forecast(scope='action_outcome')],[outcome()])
        with self.assertRaises(ValueError): join_outcomes([forecast(scope='world_model_confidence')],[outcome()],scope='world_model_confidence')

    def test_probability_timestamp_validation(self):
        for p in (-.1,1.1,float('nan'),True):
            with self.assertRaises(ValueError): forecast(probability=p)
        with self.assertRaises(ValueError): forecast(forecast_timestamp='2026-01-01')
        with self.assertRaises(ValueError): forecast(information_cutoff='2027-01-01T00:00:00+00:00')
        with self.assertRaises(ValueError): forecast(confidence_status='calibrated')

    def test_known_binary_metrics_and_ties(self):
        values=[dict(probability=p,outcome=y,task_group=str(i)) for i,(p,y) in enumerate([(.25,False),(.75,True)])]
        report=evaluate_metrics(values,bins=2)
        self.assertAlmostEqual(report['brier_score'],.0625)
        self.assertAlmostEqual(report['log_loss'],-math.log(.75))
        self.assertAlmostEqual(report['ece'],.25)
        self.assertEqual(report['roc_auc'],1)
        self.assertEqual(report['average_precision'],1)
        self.assertEqual(report['risk_coverage'][-1]['coverage'],1)
        tied=[{**r,'probability':.5} for r in values]
        self.assertEqual(evaluate_metrics(tied)['average_precision'],.5)
        self.assertEqual(len(evaluate_metrics(tied)['risk_coverage']),1)
        self.assertIsNone(evaluate_metrics([])['brier_score'])

    def test_clipping_single_class_and_no_text_labels(self):
        report=evaluate_metrics([dict(probability=0,outcome=True,task_group='a')])
        self.assertTrue(math.isfinite(report['log_loss']))
        self.assertIsNone(report['roc_auc'])
        self.assertIsNone(report['confidence_intervals']['brier_score'])
        with self.assertRaises(ValueError): evaluate_metrics([dict(probability=.5,outcome='success')])

    def test_fit_transform_strict_separation(self):
        training=rows()
        testing=rows('test','held')
        for method in ('none','platt','isotonic'):
            model=fit(training,method)
            result=transform(model,testing)
            self.assertEqual(len(result),len(testing))
            self.assertTrue(all(0<=r['probability']<=1 for r in result))
            with self.assertRaises(ValueError): fit(testing,method)
            with self.assertRaises(ValueError): transform(model,training)
            leaked=copy.deepcopy(testing);leaked[0]['provenance']['scenario_fingerprint']='s0'
            with self.assertRaises(ValueError): transform(model,leaked)

    def test_isotonic_pools_ties_and_adjacent_violators(self):
        rr=[dict(probability=p,outcome=y,task_group=str(i),trial_seed=0,split='train',provenance={'scenario_fingerprint':str(i)})
            for i,(p,y) in enumerate([(.1,True),(.1,False),(.2,False),(.8,True)])]
        model=fit(rr,'isotonic',min_groups=2)
        self.assertAlmostEqual(model['parameters']['y'][0],1/3)
        result=transform(model,[{**r,'task_group':'held'+r['task_group'],'split':'test','provenance':{'scenario_fingerprint':'held'+r['task_group']}} for r in rr])
        self.assertAlmostEqual(result[0]['probability'],1/3)
        self.assertAlmostEqual(result[2]['probability'],1/3)
        self.assertTrue(all(a['probability']<=b['probability'] for a,b in zip(result,result[1:])))

    def test_cluster_statistics_reproducible_and_holm(self):
        a=[dict(task_group=str(i//2),trial_seed=i%2,value=0) for i in range(8)]
        b=[{**r,'value':1} for r in a]
        x=paired_effect(a,b,'value',seed=7)
        self.assertEqual(x,paired_effect(a,b,'value',seed=7))
        self.assertEqual(x['independent_groups'],4)
        self.assertEqual(x['decision'],'INCONCLUSIVE')
        self.assertEqual(holm([.01,.04,.03]),[.03,.06,.06])
        with self.assertRaises(ValueError): paired_effect(a,b[:-1],'value')


if __name__=='__main__': unittest.main()
