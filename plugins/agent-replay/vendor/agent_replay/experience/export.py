"""Portable, qualified refund curriculum and evaluated-experience exports."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile

from ..serialization import canonical, digest
from ..storage import read, save
from ..rollout.artifacts import LocalObjects
from .contracts import validate
from .evidence import inspect_run
from .evaluation import evaluate
from .stages import Stages

VERSION = 'refund-export.v1'
SPLITS = ('train', 'validation', 'test')


def _task_key(entry):
    # Ignore wording and substituted entity IDs when identifying equivalent tasks.
    policy = {k: v for k, v in entry['requirement'].items() if k not in ('order_id', 'authority')}
    state = entry['environment']['initial_state']
    return digest({'policy': policy, 'lookup': {k: v for k, v in state.items() if k.startswith('lookup_')}})


def assign_splits(entries, *, seed=0, ratios=(0.8, 0.1, 0.1), require_nonempty=False):
    if type(seed) is not int or len(ratios) != 3 or any(type(x) not in (int, float) or not 0 <= x <= 1 for x in ratios) or abs(sum(ratios) - 1) > 1e-9:
        raise ValueError('Split ratios must be three finite fractions summing to one')
    entries = sorted(entries, key=lambda e: e['scenario']['id'])
    roots = list(range(len(entries)))
    def find(i):
        while roots[i] != i:
            roots[i] = roots[roots[i]]
            i = roots[i]
        return i
    owners = {}
    for i, entry in enumerate(entries):
        scenario = entry['scenario']
        tokens = [('family', scenario['family_id']), ('cluster', scenario['parent_cluster_id']),
                  ('task', _task_key(entry)), *[('failure', x) for x in scenario['parent_failure_ids']],
                  *[('source', x) for x in entry.get('_source_digests', [])]]
        for token in tokens:
            if token in owners:
                roots[find(i)] = find(owners[token])
            else:
                owners[token] = i
    groups = defaultdict(list)
    for i, entry in enumerate(entries):
        groups[find(i)].append(entry['scenario']['id'])
    assigned, sizes = {}, [0, 0, 0]
    groups = sorted(groups.values(), key=lambda ids: digest({'seed': seed, 'members': sorted(ids)}))
    total = len(entries)
    active = [i for i, ratio in enumerate(ratios) if ratio > 0]
    if require_nonempty and len(groups) < len(active):
        raise ValueError('Insufficient independent groups for nonempty splits')
    for position, ids in enumerate(groups):
        # Whole connected lineages go together; never split siblings to fill a quota.
        choices = [i for i, ratio in enumerate(ratios) if ratio > 0]
        empty = [i for i in active if sizes[i] == 0]
        if require_nonempty and len(groups) - position <= len(empty):
            choices = empty
        chosen = max(choices, key=lambda i: (ratios[i] * total - sizes[i], -i))
        group_id = digest({'members': sorted(ids)})
        for scenario_id in ids:
            assigned[scenario_id] = {'split': SPLITS[chosen], 'group_id': group_id}
        sizes[chosen] += len(ids)
    return assigned


def _closure(bundle, report, evidence, objects):
    values = {}
    def add(value):
        key = digest(value)
        values[key] = value
        return key
    def fetch(key):
        if key not in values:
            values[key] = objects.get(key)
        return values[key]
    add(bundle); add(report); add(evidence['source']); add(evidence['dataset']); add(evidence['parent'])
    # Retain every original/normalized source, association artifact and pointer proof.
    for record in evidence['dataset']['records']:
        add(record)
        for field in ('trace_digest', 'normalized_digest'):
            fetch(record['source'][field])
        for ref in record['evidence']:
            fetch(ref['artifact_digest'])
    for key in evidence['dataset']['input_digests']:
        fetch(key)
    for entry in bundle['entries']:
        for value in entry.values():
            add(value)
    for q in report.get('qualification', {}).values():
        add(q)
        for witness in q['witnesses'].values():
            fetch(witness['trace_digest']); fetch(witness['regression_digest'])
    for row in evidence['rows']:
        if row['result'] is None:
            continue
        add(row['result']); add(row['trace'])
        for ref in row['result']['evidence']:
            fetch(ref['artifact_digest'])
    return values


def _eligible_row(row, qualifications):
    result, trace = row['result'], row['trace']
    if result is None:
        return 'Missing candidate trace'
    if result['scenario_digest'] not in qualifications:
        return 'Verifier is not currently qualified'
    if row['reason']:
        return row['reason']
    if row['verdict'] not in ('pass', 'fail'):
        return 'Candidate verdict is ' + row['verdict']
    if trace['execution']['status'] != 'completed' or trace.get('capture_health', {}).get('state') != 'complete':
        return 'Incomplete candidate execution or capture'
    if trace['fidelity'].get('level') != 'controlled_boundaries':
        return 'Unsupported candidate fidelity'
    for envelope in (trace['input'], trace['output']):
        if not envelope.get('present') or envelope.get('replayability') != 'complete':
            return 'Unavailable or redacted candidate input/output'
    for step in trace['steps']:
        if step['input'].get('replayability') != 'complete' or not step['input'].get('present'):
            return 'Unavailable boundary input'
        if step['execution']['status'] == 'completed' and (not step['output'].get('present') or step['output'].get('replayability') != 'complete'):
            return 'Unavailable completed boundary output'
    return None


def export_dataset(bundle, report, output, *, directory, profile='evaluated-experience', seed=0, ratios=(0.8, 0.1, 0.1)):
    if profile not in ('scenario', 'evaluated-experience'):
        raise ValueError('Refund supports scenario/evaluated-experience profiles. Policy-rollout needs actual action tokens, behavior log probabilities and policy versions; these cannot be fabricated.')
    objects = Stages(directory).objects
    evidence = inspect_run(bundle, report, objects)
    evaluation = evaluate(bundle, report, directory=directory)
    assignments = assign_splits(list(evidence['entries'].values()), seed=seed, ratios=ratios)
    records, exclusions = [], list(evidence['exclusions'])
    candidates = [({'entry': e, 'result': None, 'trace': None}, None) for key, e in sorted(evidence['entries'].items()) if key in evidence['qualifications']] if profile == 'scenario' else [(row, _eligible_row(row, evidence['qualifications'])) for row in evidence['rows']]
    for row, reason in candidates:
        entry, result = row['entry'], row['result']
        if reason:
            exclusions.append({'artifact_digest': digest(result) if result else digest(entry['scenario']), 'reason': reason})
            continue
        scenario = entry['scenario']
        assignment = assignments[scenario['id']]
        qualification = evidence['qualifications'][digest(scenario)]
        core = {'schema_version': 'experience.export-record.v1', 'profile': profile,
            'scenario_digest': digest(scenario), 'environment_digest': digest(entry['environment']),
            'verifier_digest': digest(entry['verifier']), 'qualification_digest': digest(qualification),
            'requirement_digest': digest(entry['requirement']), 'source_trace_digest': digest(evidence['source']),
            'parent_failure_ids': scenario['parent_failure_ids'], 'parent_cluster_id': scenario['parent_cluster_id'],
            'family_id': scenario['family_id'], 'input': scenario['input'], 'seed': scenario['seed'],
            **assignment, 'result_digest': digest(result) if result else None,
            'trace_digest': result['trace_digest'] if result else None,
            'candidate': result['candidate'] if result else None, 'verdict': row.get('verdict') if result else None,
            'reward': (1 if row['verdict'] == 'pass' else 0) if result else None}
        records.append(validate({'id': digest(core), **core}, 'experience-export-record'))
    records.sort(key=lambda r: r['id'])
    values = _closure(bundle, report, evidence, objects)
    values[digest(evaluation)] = evaluation
    if Path(output).is_symlink():
        raise ValueError('Export destination cannot be a symlink')
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.refund-export-', dir=output.parent))
    try:
        store = LocalObjects(temporary / 'objects')
        for value in values.values():
            store.put(value)
        (temporary / 'records.jsonl').write_text(''.join(canonical(r) + '\n' for r in records), encoding='utf-8')
        save(temporary / 'evaluation.json', evaluation)
        artifacts = [{'path': path.relative_to(temporary).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                     for path in sorted(temporary.rglob('*')) if path.is_file()]
        splits = {name: [r['id'] for r in records if r['split'] == name] for name in SPLITS}
        empty = [name for name in SPLITS if not splits[name]]
        manifest = validate({'schema_version': 'experience.dataset.v1', 'profile': profile,
            'schema_versions': sorted({value['schema_version'] for value in values.values() if isinstance(value, dict) and isinstance(value.get('schema_version'), str)} | {'experience.export-record.v1'}),
            'artifacts': artifacts, 'splits': splits,
            'eligibility': {'state': 'eligible' if records else 'blocked', 'reasons': [] if records else ['No currently qualified eligible records']},
            'exclusions': sorted({(x['artifact_digest'], x['reason']): x for x in exclusions}.values(), key=lambda x: (x['artifact_digest'], x['reason'])),
            'generation': {'implementation_version': VERSION, 'seed': seed, 'requested_split_ratios': list(ratios),
                'bundle_digest': digest(bundle), 'report_digest': digest(report), 'evaluation_digest': digest(evaluation),
                'record_count': len(records), 'split_group_count': len({r['group_id'] for r in records}),
                'empty_splits': empty, 'split_policy': 'connected-family-parent-and-equivalent-task.v1',
                'reward_policy': 'qualified_completed_pass=1_fail=0; other verdicts excluded',
                'training_profile': 'environment-curriculum' if profile == 'scenario' else 'evaluated-agent-experience',
                'policy_rollout_ready': False}}, 'experience-dataset')
        save(temporary / 'manifest.json', manifest)
        validate_export(temporary)
        if output.exists():
            old = validate_export(output)
            if old != manifest:
                raise ValueError('Export destination already contains a different dataset')
        else:
            temporary.rename(output)
        return {'manifest': str(output / 'manifest.json'), 'manifest_digest': digest(manifest),
                'record_count': len(records), 'splits': {k: len(v) for k, v in splits.items()}, 'empty_splits': empty, 'reused': temporary.exists()}
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def validate_export(directory):
    """Verify a moved export from its own objects, never from the original store."""
    root = Path(directory).resolve()
    manifest_path = root / 'manifest.json'
    if manifest_path.is_symlink():
        raise ValueError('Export manifest cannot be a symlink')
    manifest = validate(read(manifest_path, limit=100*1024*1024), 'experience-dataset')
    if manifest['profile'] not in ('scenario', 'evaluated-experience'):
        raise ValueError('Unsupported refund export profile')
    expected = set()
    for artifact in manifest['artifacts']:
        name = artifact['path']; relative = PurePosixPath(name)
        if relative.is_absolute() or '\\' in name or any(p in ('', '.', '..') for p in name.split('/')) or name == 'manifest.json':
            raise ValueError('Unsafe export artifact path')
        if name in expected:
            raise ValueError('Duplicate export artifact')
        path = root / relative
        if not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('Export artifact escapes the dataset or uses a symlink')
        if hashlib.sha256(path.read_bytes()).hexdigest() != artifact['sha256']:
            raise ValueError('Export artifact checksum mismatch')
        expected.add(name)
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p != manifest_path}
    if actual != expected or any(p.is_symlink() for p in root.rglob('*')):
        raise ValueError('Export has unlisted or missing artifacts')
    objects = LocalObjects(root / 'objects')
    generation = manifest['generation']
    bundle = objects.get(generation['bundle_digest']); report = objects.get(generation['report_digest'])
    evidence = inspect_run(bundle, report, objects)
    current = evaluate(bundle, report, directory=root, persist=False)
    if digest(current) != generation['evaluation_digest'] or read(root / 'evaluation.json') != current:
        raise ValueError('Export evaluation cannot be reproduced')
    assignments = assign_splits(list(evidence['entries'].values()), seed=generation['seed'], ratios=generation['requested_split_ratios'])
    lines = (root / 'records.jsonl').read_text(encoding='utf-8').splitlines()
    records = [validate(json.loads(line), 'experience-export-record') for line in lines]
    ids, groups = set(), {}
    eligible_results = {digest(row['result']): row for row in evidence['rows'] if _eligible_row(row, evidence['qualifications']) is None}
    for record in records:
        from .mutation import check_id
        check_id(record)
        if record['id'] in ids:
            raise ValueError('Duplicate export record')
        ids.add(record['id'])
        entry = evidence['entries'].get(record['scenario_digest'])
        if entry is None or record['scenario_digest'] not in evidence['qualifications']:
            raise ValueError('Export record lacks qualified scenario evidence')
        scenario = entry['scenario']
        fields = {'environment_digest': digest(entry['environment']), 'verifier_digest': digest(entry['verifier']),
                  'requirement_digest': digest(entry['requirement']), 'qualification_digest': digest(evidence['qualifications'][digest(scenario)]),
                  'source_trace_digest': digest(evidence['source']), 'parent_failure_ids': scenario['parent_failure_ids'],
                  'parent_cluster_id': scenario['parent_cluster_id'], 'family_id': scenario['family_id'],
                  'input': scenario['input'], 'seed': scenario['seed'], 'profile': manifest['profile'], **assignments[scenario['id']]}
        if any(record[k] != value for k, value in fields.items()):
            raise ValueError('Export record lineage, content or split mismatch')
        if record['group_id'] in groups and groups[record['group_id']] != record['split']:
            raise ValueError('Related scenarios cross dataset splits')
        groups[record['group_id']] = record['split']
        if manifest['profile'] == 'evaluated-experience':
            row = eligible_results.get(record['result_digest'])
            if row is None or record['scenario_digest'] != row['result']['scenario_digest'] or any(record[k] != row['result'][k] for k in ('trace_digest', 'candidate', 'verdict')) or record['reward'] != (1 if row['verdict'] == 'pass' else 0):
                raise ValueError('Export reward or candidate evidence mismatch')
        elif manifest['profile'] != 'scenario' or any(record[k] is not None for k in ('result_digest', 'trace_digest', 'candidate', 'verdict', 'reward')):
            raise ValueError('Unsupported export profile or invented rollout fields')
    if manifest['splits'] != {name: [r['id'] for r in records if r['split'] == name] for name in SPLITS} or generation['record_count'] != len(records):
        raise ValueError('Export record inventory differs from manifest')
    expected_items = set(eligible_results) if manifest['profile'] == 'evaluated-experience' else set(evidence['qualifications'])
    actual_items = {r['result_digest'] if manifest['profile'] == 'evaluated-experience' else r['scenario_digest'] for r in records}
    if expected_items != actual_items:
        raise ValueError('Export omitted or duplicated eligible records')
    if manifest['eligibility'] != {'state': 'eligible' if records else 'blocked', 'reasons': [] if records else ['No currently qualified eligible records']}:
        raise ValueError('Export eligibility differs from record evidence')
    if generation['split_group_count'] != len(groups) or generation['empty_splits'] != [name for name in SPLITS if not manifest['splits'][name]]:
        raise ValueError('Export split metadata mismatch')
    exclusions = list(evidence['exclusions'])
    if manifest['profile'] == 'evaluated-experience':
        for row in evidence['rows']:
            reason = _eligible_row(row, evidence['qualifications'])
            if reason:
                exclusions.append({'artifact_digest': digest(row['result']) if row['result'] else digest(row['entry']['scenario']), 'reason': reason})
    exclusions = sorted({(x['artifact_digest'], x['reason']): x for x in exclusions}.values(), key=lambda x: (x['artifact_digest'], x['reason']))
    if manifest['exclusions'] != exclusions:
        raise ValueError('Export exclusion audit differs from evidence')
    if generation['implementation_version'] != VERSION or generation['split_policy'] != 'connected-family-parent-and-equivalent-task.v1' or generation['reward_policy'] != 'qualified_completed_pass=1_fail=0; other verdicts excluded':
        raise ValueError('Unsupported export generation policy')
    expected_profile = 'environment-curriculum' if manifest['profile'] == 'scenario' else 'evaluated-agent-experience'
    if generation['training_profile'] != expected_profile:
        raise ValueError('Export training profile mismatch')
    if generation.get('policy_rollout_ready') is not False:
        raise ValueError('Refund artifacts do not contain policy rollout tokens')
    return manifest


def load_dataset(directory):
    """Consumer interface: verified manifest, typed records and local evidence store."""
    manifest = validate_export(directory)
    root = Path(directory).resolve()
    records = [validate(json.loads(line), "experience-export-record")
               for line in (root / "records.jsonl").read_text(encoding="utf-8").splitlines()]
    return {"manifest": manifest, "records": records, "objects": LocalObjects(root / "objects")}
