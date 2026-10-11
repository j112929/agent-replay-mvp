import tempfile
from pathlib import Path
import unittest
from agent_replay.security.demo import demo
from agent_replay.benchmark.release import release_gate,paired_statistics
from agent_replay.storage import read,save


class ReleaseTests(unittest.TestCase):
    def test_end_to_end_security_story(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)/'demo';result=demo(root)
            self.assertEqual(result['gate'],'FAIL')
            self.assertFalse(result['canary_leaked'])
            self.assertTrue(result['state_mutation_blocked'])
            self.assertEqual(result['external_operations'],0)
            self.assertIn('behavior_changes',read(root/'candidate/reports/report.json')['comparison']['failure_analysis'][0])
            result=release_gate(root/'baseline',root/'baseline',{'critical_tags':[]})
            self.assertEqual(result['decision'],'INCONCLUSIVE');self.assertTrue(result['blocking'])
            result=release_gate(root/'baseline',root/'baseline',{'environment':'DEVELOPMENT','critical_tags':[]})
            self.assertFalse(result['blocking'])
            result=release_gate(root/'missing',root/'candidate')
            self.assertEqual(result['decision'],'INCONCLUSIVE')

    def test_statistics_and_policy_validation(self):
        result=paired_statistics({'newly_passing':[],'newly_failing':['x']*6,'matched_count':100})
        self.assertLess(result['one_sided_p_value'],.05)
        result=paired_statistics({'newly_passing':[],'newly_failing':['x'],'matched_count':100})
        self.assertGreater(result['one_sided_p_value'],.05)
        with self.assertRaises(ValueError):release_gate('missing','missing',{'alpha':float('nan')})
