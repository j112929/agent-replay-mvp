"""Public reports recompute evidence, distinguish API errors, and omit private data."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('refund_summary', Path(__file__).resolve().parents[1] / 'scripts/publish_refund_summary.py')
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)

class RefundSummaryTests(unittest.TestCase):
    def setUp(self):
        self.data = {'records': [{'family_id': 'refund/' + split, 'split': split, 'scenario_digest': split} for split in ('train', 'validation', 'test')]}
        metric = {'numerator': 1, 'denominator': 1, 'value': 1}
        self.baseline = {'splits': {split: {'candidates': {'broken': metric, 'fixed': metric}, 'success_rate_delta': 0} for split in ('validation', 'test')}}
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'report.json').write_text(json.dumps({'runs': [{'experiment_path': 'execution.json'}]}))
        (self.root / 'execution.json').write_text(json.dumps({'trace': {'execution': {'error': {'type': 'ProviderError', 'message': 'credit_balance_exhausted PRIVATE_RAW_RESPONSE'}}}}))
        self.metrics = {'scope': 'live', 'cross_model_failure_reproduction': {'state': 'unmeasured', 'numerator': 0, 'denominator': 0, 'value': None}, 'verdict_counts': {'pass': 0, 'fail': 0, 'error': 1, 'inconclusive': 0}, 'coverage': {'planned_runs': 1, 'decisive_runs': 0}, 'held_out': {'test': [{'model': '<model>', 'version': 'v1', 'planned': 1, 'successes': 0, 'decisive': 0, 'success_rate': None}]}}

    def render(self, metrics=None):
        with patch.object(summary, 'validate_collection', return_value=self.data), patch.object(summary, 'held_out', return_value=self.baseline), patch('agent_replay.experience.models.evaluate_models', return_value=metrics or self.metrics) as verifier:
            result = summary.render('dataset', self.root)
            verifier.assert_called_once_with('dataset', self.root)
            return result

    def test_quota_errors_are_unmeasured_and_never_publish_raw_evidence(self):
        result = self.render()
        self.assertIn('余额耗尽', result)
        self.assertIn('实测尚未完成', result)
        self.assertIn('0/0 (未测量)', result)
        self.assertIn('&lt;model&gt;', result)
        self.assertNotIn('PRIVATE_RAW_RESPONSE', result)
        self.assertNotIn(str(self.root), result)
        self.assertNotIn('.zip"', result)

    def test_offline_wire_evidence_cannot_claim_real_measurements(self):
        self.metrics['scope'] = 'offline-wire-test'
        with self.assertRaisesRegex(ValueError, 'Offline wire'):
            self.render()

    def test_complete_live_measurements_show_success_and_reproduction_denominators(self):
        (self.root / 'report.json').write_text('{"runs": []}')
        self.metrics['cross_model_failure_reproduction'] = {'state': 'measured', 'numerator': 0, 'denominator': 1, 'value': 0}
        self.metrics['coverage']['decisive_runs'] = 1
        self.metrics['verdict_counts'].update({'pass': 1, 'error': 0})
        self.metrics['held_out']['test'][0].update({'successes': 1, 'decisive': 1, 'success_rate': 1})
        result = self.render()
        self.assertIn('实测完成', result)
        self.assertIn('1/1 (100.0%)', result)
        self.assertIn('0/1 (0.0%)', result)
        self.assertNotIn('余额耗尽', result)

    def test_json_protocol_failures_are_disclosed_without_exposing_responses(self):
        (self.root / 'execution.json').write_text(json.dumps({'trace': {'execution': {'error': {'type': 'JSONDecodeError', 'message': 'PRIVATE_RAW_RESPONSE'}}}}))
        result = self.render()
        self.assertIn('1 次原始 JSON 解析失败', result)
        self.assertIn('未自动剥离 Markdown', result)
        self.assertNotIn('PRIVATE_RAW_RESPONSE', result)

    def test_english_preserves_metrics_and_translates_disclosures(self):
        with patch.object(summary, 'validate_collection', return_value=self.data), patch.object(summary, 'held_out', return_value=self.baseline), patch('agent_replay.experience.models.evaluate_models', return_value=self.metrics):
            result = summary.render('dataset', self.root, 'en')
        self.assertIn('lang="en"', result)
        self.assertIn('API credits were exhausted', result)
        self.assertIn('0/0 (Unmeasured)', result)
        self.assertIn('data-language="en" aria-current="true"', result)
        self.assertNotIn('实测', result)
        self.assertIn('/refund/language.js', result)
        self.assertIn('Main navigation', result)
        self.assertIn('Overview', result)
        self.assertIn('Evaluation reports', result)
        self.assertIn('aria-current="page"', result)

    def test_invalid_evidence_is_not_silently_published(self):
        with patch.object(summary, 'validate_collection', return_value=self.data), patch.object(summary, 'held_out', return_value=self.baseline), patch('agent_replay.experience.models.evaluate_models', side_effect=ValueError('binding mismatch')):
            with self.assertRaisesRegex(ValueError, 'binding mismatch'):
                summary.render('dataset', self.root)
