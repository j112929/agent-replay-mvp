"""E/F evaluation and portable dataset acceptance on actual refund executions."""
import copy
import json
import hashlib
from pathlib import Path
import shutil
import tempfile
import unittest

from agent_replay.capture import capture
from agent_replay.demo import broken
from agent_replay.storage import read, save
from agent_replay.serialization import digest
from agent_replay.experience.pipeline import ingest, cluster, mutate
from agent_replay.experience.mutation import DEFAULT_REQUIREMENT, identified
from agent_replay.experience.compiler import refund_loop
from agent_replay.experience.stages import Stages
from agent_replay.experience.evaluation import evaluate, write_evaluation
from agent_replay.experience.export import export_dataset, validate_export, assign_splits
from agent_replay.experience.contracts import validate, SCHEMAS
from agent_replay.experience.cli import main


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed_temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.seed_temp.name)
        with capture('Refund E/F', cls.root / 'seed', input={'order_id': 'o_1042'}, durability='sync') as run:
            run.set_output(broken(run)); run.fail('Refund exceeds approval', category='assertion')
        source = read(run.path)
        dataset = ingest([source], directory=cls.root)['value']
        parent = cluster(dataset, directory=cls.root, config={'min_cluster_size': 1})['value']['clusters'][0]
        cls.bundle = mutate(dataset, parent, DEFAULT_REQUIREMENT, directory=cls.root,
                            operators=['request_above', 'lookup_transient'])['value']
        cls.report = refund_loop(cls.bundle, source, cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.seed_temp.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'run'
        shutil.copytree(self.root / 'objects', self.directory / 'objects')
        self.objects = Stages(self.directory).objects
        self.output = Path(self.temp.name) / 'export'

    def export(self, **kwargs):
        return export_dataset(self.bundle, self.report, self.output, directory=self.directory, **kwargs)

    def test_metrics_have_explicit_denominators_and_unmeasured_states(self):
        value = evaluate(self.bundle, self.report, directory=self.directory)
        self.assertEqual(value['scenario_count'], 2)
        self.assertEqual(value['result_count'], 6)
        self.assertEqual(value['verdict_counts'], {'pass': 2, 'fail': 4})
        self.assertEqual(value['metrics']['success_rate']['value'], 1/3)
        self.assertEqual(value['metrics']['mandatory_negative_rejection_rate']['denominator'], 14)
        self.assertEqual(value['metrics']['repeat_candidate_verdict_consistency']['value'], 1)
        self.assertEqual(value['metrics']['cross_candidate_failure_reproduction']['value'], 0)
        for name in ('new_failure_mode_rate', 'cross_model_failure_reproduction', 'held_out_task_success_improvement'):
            self.assertIsNone(value['metrics'][name]['value'])
            self.assertEqual(value['metrics'][name]['state'], 'unmeasured')
        self.assertEqual([c['model_calls'] for c in value['candidates']], [0, 0])
        self.assertTrue(write_evaluation(value, self.directory / 'report')['html'].endswith('.html'))

    def test_baseline_novelty_recomputes_stored_evidence(self):
        baseline = evaluate(self.bundle, self.report, directory=self.directory)
        value = evaluate(self.bundle, self.report, directory=self.directory, baseline=baseline)
        self.assertEqual(value['metrics']['new_failure_mode_rate']['value'], 0)
        self.assertEqual(value['baseline_digest'], digest(baseline))
        forged = copy.deepcopy(baseline)
        forged['observed_failure_modes'] = {}
        self.objects.put(forged)
        with self.assertRaises(ValueError):
            evaluate(self.bundle, self.report, directory=self.directory, baseline=forged)

    def test_exact_duplicate_results_do_not_inflate_metrics(self):
        report = copy.deepcopy(self.report)
        report['suites']['duplicate-copy'] = copy.deepcopy(report['suites']['before'])
        self.assertEqual(evaluate(self.bundle, report, directory=self.directory)['result_count'], 6)
        self.assertEqual(self.report['metrics']['execution_rate'], 1)

    def test_reward_lineage_and_complete_portable_qualification(self):
        outcome = self.export()
        manifest = validate_export(self.output)
        records = [json.loads(line) for line in (self.output / 'records.jsonl').read_text().splitlines()]
        self.assertEqual(outcome['record_count'], 6)
        self.assertEqual(sum(r['reward'] for r in records), 2)
        self.assertEqual(outcome['splits'], {'train': 6, 'validation': 0, 'test': 0})
        self.assertEqual(outcome['empty_splits'], ['validation', 'test'])
        self.assertFalse(manifest['generation']['policy_rollout_ready'])
        copied = Path(self.temp.name) / 'moved'
        shutil.copytree(self.output, copied)
        shutil.rmtree(self.directory)
        self.assertEqual(validate_export(copied), manifest)
        for record in records:
            q = Stages(copied).objects.get(record['qualification_digest'])
            self.assertEqual(q['state'], 'qualified')
            self.assertEqual(len(q['witnesses']), 8)
            self.assertEqual(record['parent_failure_ids'], self.bundle['entries'][0]['scenario']['parent_failure_ids'])
            self.assertNotIn('_environment', record['input'])

    def test_scenario_profile_has_no_invented_rewards_or_policy_tokens(self):
        self.export(profile='scenario')
        records = [json.loads(line) for line in (self.output / 'records.jsonl').read_text().splitlines()]
        self.assertEqual(len(records), 2)
        self.assertTrue(all(r['reward'] is None and r['candidate'] is None and r['trace_digest'] is None for r in records))
        with self.assertRaisesRegex(ValueError, 'cannot be fabricated'):
            export_dataset(self.bundle, self.report, Path(self.temp.name) / 'policy', directory=self.directory, profile='policy-rollout')

    def test_idempotent_export_refuses_different_destination_contents(self):
        first = self.export()
        second = self.export()
        self.assertEqual(first['manifest_digest'], second['manifest_digest'])
        self.assertTrue(second['reused'])
        with self.assertRaises(ValueError):
            self.export(profile='scenario')
        self.assertEqual(validate_export(self.output)['profile'], 'evaluated-experience')

    def test_corruption_forged_verdict_and_missing_objects_block_export(self):
        report = copy.deepcopy(self.report)
        report['suites']['before']['results'][0]['verdict'] = 'pass'
        with self.assertRaises(ValueError):
            export_dataset(self.bundle, report, self.output, directory=self.directory)
        key = self.report['suites']['before']['results'][0]['scenario_result_digest']
        self.objects.path(key).write_text('{}')
        with self.assertRaises(ValueError):
            self.export()
        self.assertFalse(self.output.exists())

    def test_missing_qualification_preserves_exclusions_and_blocks_labels(self):
        report = copy.deepcopy(self.report)
        report['qualification'] = {}
        value = evaluate(self.bundle, report, directory=self.directory)
        self.assertEqual(value['metrics']['qualification_rate']['value'], 0)
        export_dataset(self.bundle, report, self.output, directory=self.directory)
        manifest = validate_export(self.output)
        self.assertEqual(manifest['eligibility']['state'], 'blocked')
        self.assertEqual(manifest['generation']['record_count'], 0)
        self.assertTrue(manifest['exclusions'])

    def test_stale_qualification_is_excluded_not_relabeled(self):
        report = copy.deepcopy(self.report)
        for q in report['qualification'].values():
            q['implementation_digest'] = 'f' * 64
        value = evaluate(self.bundle, report, directory=self.directory)
        self.assertEqual(value['metrics']['qualification_rate']['value'], 0)
        self.assertTrue(all('Stale' in x['reason'] for x in value['exclusions']))

    def test_split_groups_keep_parent_family_and_equivalent_tasks_together(self):
        a = copy.deepcopy(self.bundle['entries'][0]); b = copy.deepcopy(a)
        b['scenario']['id'] = '1'*64
        b['scenario']['family_id'] = 'different-family'
        b['scenario']['parent_cluster_id'] = '2'*64
        b['scenario']['parent_failure_ids'] = ['3'*64]
        assigned = assign_splits([a, b])
        self.assertEqual(len({x['group_id'] for x in assigned.values()}), 1)
        self.assertEqual(assigned, assign_splits([b, a]))
        c = copy.deepcopy(b); c['scenario']['id'] = '4'*64
        c['requirement']['approved_minor'] += 100
        c['scenario']['parent_failure_ids'] = ['5'*64]; c['scenario']['parent_cluster_id'] = '6'*64
        c['scenario']['family_id'] = 'third-family'
        self.assertEqual(len({x['group_id'] for x in assign_splits([a, b, c]).values()}), 2)
        for ratios in ((1, 1, 1), (float('nan'), 0, 0), (True, 0, 0)):
            with self.assertRaises(ValueError):
                assign_splits([a], ratios=ratios)

    def test_manifest_checksum_path_traversal_and_unlisted_files_rejected(self):
        self.export()
        manifest = read(self.output / 'manifest.json')
        bad = copy.deepcopy(manifest); bad['artifacts'][0]['path'] = '../outside'
        save(self.output / 'manifest.json', bad)
        with self.assertRaises(ValueError):
            validate_export(self.output)
        save(self.output / 'manifest.json', manifest)
        (self.output / 'extra.txt').write_text('unlisted')
        with self.assertRaises(ValueError):
            validate_export(self.output)
        (self.output / 'extra.txt').unlink()
        (self.output / 'records.jsonl').write_text('')
        with self.assertRaises(ValueError):
            validate_export(self.output)

    def test_dropping_rows_with_rehashed_manifest_does_not_hide_evidence(self):
        self.export()
        path = self.output / 'records.jsonl'
        records = [json.loads(line) for line in path.read_text().splitlines()]
        removed = records.pop()
        path.write_text(''.join(json.dumps(r) + '\n' for r in records))
        manifest = read(self.output / 'manifest.json')
        for a in manifest['artifacts']:
            if a['path'] == 'records.jsonl':a['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest['splits'][removed['split']].remove(removed['id'])
        manifest['generation']['record_count'] -= 1
        save(self.output / 'manifest.json', manifest)
        with self.assertRaises(ValueError):
            validate_export(self.output)

    def test_inconclusive_missing_trace_is_diagnostic_and_not_rewarded(self):
        report = copy.deepcopy(self.report)
        row = report['suites']['before']['results'][0]
        row.pop('scenario_result_digest'); row.pop('candidate_trace_id')
        row['verdict'] = 'inconclusive'; row['reason'] = 'Interrupted worker'
        value = evaluate(self.bundle, report, directory=self.directory)
        self.assertEqual(value['verdict_counts']['inconclusive'], 1)
        self.assertEqual(value['metrics']['execution_rate']['numerator'], 5)
        result = export_dataset(self.bundle, report, self.output, directory=self.directory)
        self.assertEqual(result['record_count'], 5)
        self.assertTrue(any('Missing candidate trace' in e['reason'] for e in validate_export(self.output)['exclusions']))

    def test_forged_rewards_rejected_even_when_file_and_record_hashes_are_updated(self):
        from agent_replay.serialization import canonical
        self.export()
        path = self.output / 'records.jsonl'
        records = [json.loads(line) for line in path.read_text().splitlines()]
        record = next(r for r in records if r['verdict'] == 'fail')
        old_id = record['id']; record['verdict'] = 'pass'; record['reward'] = 1
        record['id'] = digest({k: v for k, v in record.items() if k != 'id'})
        path.write_text(''.join(canonical(r) + '\n' for r in records))
        manifest = read(self.output / 'manifest.json')
        for artifact in manifest['artifacts']:
            if artifact['path'] == 'records.jsonl':artifact['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest['splits'][record['split']] = [record['id'] if key == old_id else key for key in manifest['splits'][record['split']]]
        save(self.output / 'manifest.json', manifest)
        with self.assertRaises(ValueError):validate_export(self.output)

    def test_cli_evaluate_export_verify_and_profile_error_codes(self):
        import contextlib, io
        bundle = self.directory / 'bundle.json'; report = self.directory / 'report.json'
        save(bundle, self.bundle); save(report, self.report)
        common = [str(bundle), '--report', str(report), '--directory', str(self.directory)]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['evaluate', *common]), 0)
            self.assertEqual(main(['export', *common, '--destination', str(self.output)]), 0)
            self.assertEqual(main(['verify-export', str(self.output)]), 0)
            self.assertEqual(main(['export', *common, '--destination', str(self.output), '--profile', 'policy-rollout']), 2)
            self.assertEqual(main(['verify-export', str(self.output), '--output', str(self.output / 'extra.json')]), 2)
        self.assertFalse((self.output / 'extra.json').exists())

    def test_symlink_artifact_is_rejected_and_export_validation_is_read_only(self):
        self.export()
        hashes = {p.relative_to(self.output): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.output.rglob('*') if p.is_file()}
        validate_export(self.output)
        self.assertEqual(hashes, {p.relative_to(self.output): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.output.rglob('*') if p.is_file()})
        path = self.output / 'records.jsonl'; target = Path(self.temp.name) / 'records-backup'
        shutil.copyfile(path, target); path.unlink(); path.symlink_to(target)
        with self.assertRaises(ValueError):validate_export(self.output)

    def test_export_exclusion_audit_and_empty_profile_cannot_be_forged(self):
        report = copy.deepcopy(self.report); report['qualification'] = {}
        export_dataset(self.bundle, report, self.output, directory=self.directory)
        manifest = read(self.output / 'manifest.json')
        self.assertTrue(manifest['exclusions'])
        changed = copy.deepcopy(manifest); changed['exclusions'] = []
        save(self.output / 'manifest.json', changed)
        with self.assertRaises(ValueError):validate_export(self.output)
        changed = copy.deepcopy(manifest); changed['profile'] = 'policy-rollout'
        save(self.output / 'manifest.json', changed)
        with self.assertRaises(ValueError):validate_export(self.output)

    def test_independent_split_groups_can_fill_all_partitions(self):
        entries = []
        for i in range(11):
            entry = copy.deepcopy(self.bundle['entries'][0])
            entry['scenario']['id'] = digest({'id': i})
            entry['scenario']['family_id'] = 'family-' + str(i)
            entry['scenario']['parent_cluster_id'] = digest({'cluster': i})
            entry['scenario']['parent_failure_ids'] = [digest({'failure': i})]
            entry['requirement']['approved_minor'] += i * 100
            entries.append(entry)
        assignments = assign_splits(entries, seed=7)
        self.assertEqual({a['split'] for a in assignments.values()}, {'train', 'validation', 'test'})
        self.assertEqual(assignments, assign_splits(list(reversed(entries)), seed=7))
        self.assertNotEqual(assignments, assign_splits(entries, seed=8))

    def test_runtime_and_published_schema_accept_records_and_measurements(self):
        import jsonschema
        value = evaluate(self.bundle, self.report, directory=self.directory)
        jsonschema.validate(value, SCHEMAS['experience-evaluation'])
        self.export()
        for line in (self.output / 'records.jsonl').read_text().splitlines():
            record = json.loads(line)
            jsonschema.validate(record, SCHEMAS['experience-export-record'])
            validate(record)
        value['metrics']['execution_rate']['denominator'] = 0
        with self.assertRaises(ValueError):validate(value)


if __name__ == '__main__':unittest.main()
