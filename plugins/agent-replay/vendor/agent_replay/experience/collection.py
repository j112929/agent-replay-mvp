"""Multi-family portable curricula composed from verified v1 refund exports.

Children keep their complete qualification closure. Global splits are recomputed
from all children, including shared raw/normalized source evidence.
"""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from ..capture import capture
from ..serialization import canonical, digest
from ..storage import read, save
from . import refund
from .compiler import refund_loop
from .contracts import validate
from .evidence import inspect_run
from .export import assign_splits, export_dataset, load_dataset, SPLITS
from .mutation import DEFAULT_REQUIREMENT
from .pipeline import ingest, cluster, mutate
from .stages import Stages

VERSION = 'refund-collection.v1'
# Separate source executions and distinct policies; names never establish independence.
FAMILIES = (
    ('approval-cap', 3000, 29900, 0, 3100, 'USD'),
    ('remaining-balance', 5000, 7000, 4500, 6000, 'USD'),
    ('request-cap', 9000, 15000, 0, 2200, 'EUR'),
    ('prior-partial-refund', 4000, 12000, 3000, 4300, 'GBP'),
    ('near-exhausted', 1800, 8000, 7900, 2100, 'JPY'),
    ('small-approval', 25, 600, 0, 40, 'USD'),
    ('large-payment', 35000, 900000, 120000, 40000, 'EUR'),
    ('exact-remaining', 6000, 11000, 5000, 6200, 'CAD'),
    ('request-after-refund', 7000, 17000, 11000, 1500, 'AUD'),
    ('low-remaining', 2500, 14000, 13200, 2900, 'GBP'),
    ('approval-after-refund', 900, 9500, 700, 1100, 'CAD'),
    ('small-request', 2200, 4700, 100, 15, 'JPY'),
)


def _source(requirement, root, name):
    order = {k: requirement[k] for k in ('approved_minor', 'paid_minor', 'refunded_minor')}
    env = {'initial_state': {'orders': {requirement['order_id']: order},
                            'currency': requirement['currency']}}
    inputs = {'order_id': requirement['order_id'], 'requested_minor': requirement['requested_minor'], '_environment': env}
    with capture('Refund ' + name, root / 'seed', input=inputs, entrypoint_id='broken', durability='sync') as run:
        run.trace['metadata']['task_family_id'] = 'refund/' + name
        run.state = refund.reset(inputs)
        run.set_output(refund.broken(run))
        run.trace['metadata']['environment_state'] = refund.snapshot(run.state)
        run.fail('Refund violates explicit family policy', category='assertion')
        refund.cleanup(run.state)
    return read(run.path)


def demo(directory, *, seed=0, budget=2, family_count=12):
    if type(family_count) is not int or not 3 <= family_count <= len(FAMILIES):
        raise ValueError('Use 3–12 independent refund families')
    root = Path(directory).resolve()
    runs = []
    for index, (name, approved, paid, refunded, requested, currency) in enumerate(FAMILIES[:family_count]):
        child = root / name
        requirement = {**DEFAULT_REQUIREMENT, 'authority': 'synthetic-refund-families.v1',
                       'order_id': 'family-' + name, 'approved_minor': approved, 'paid_minor': paid,
                       'refunded_minor': refunded, 'requested_minor': requested, 'currency': currency}
        source = _source(requirement, child, name)
        dataset = ingest([source], directory=child)['value']
        parent = cluster(dataset, directory=child, config={'min_cluster_size': 1})['value']['clusters'][0]
        # Keep a policy-preserving mutation: request-cap families must not be turned into approval-cap tasks.
        bundle = mutate(dataset, parent, requirement, directory=child, seed=seed + index,
                        budget=budget, operators=['wording', 'lookup_transient'])['value']
        save(child / 'scenarios.json', bundle)
        report = refund_loop(bundle, source, child)
        runs.append({'name': name, 'bundle': bundle, 'report': report, 'directory': str(child)})
    return runs


def _inputs(children):
    entries, records = [], []
    for name, data in children:
        store = data['objects']; manifest = data['manifest']
        bundle = store.get(manifest['generation']['bundle_digest'])
        report = store.get(manifest['generation']['report_digest'])
        evidence = inspect_run(bundle, report, store)
        sources = sorted({r['source'][k] for r in evidence['dataset']['records'] for k in ('trace_digest', 'normalized_digest')})
        for entry in evidence['entries'].values():
            entries.append({**entry, '_source_digests': sources})
        for record in data['records']:
            records.append((name, record))
    if len({e['scenario']['id'] for e in entries}) != len(entries):
        raise ValueError('Duplicate scenarios across collection children')
    return entries, records


def _records(children, seed, ratios):
    entries, originals = _inputs(children)
    assignments = assign_splits(entries, seed=seed, ratios=ratios, require_nonempty=True)
    records = []
    for child, original in originals:
        core = {k: v for k, v in original.items() if k != 'id'}
        # scenario.id equals its canonical core hash; scenario_digest includes id.
        # Look up the scenario ID through the qualified object instead.
        data = dict(children)[child]
        core.update(assignments[data['objects'].get(original['scenario_digest'])['id']])
        core.update(schema_version='experience.collection-record.v1', child=child, original_record_id=original['id'])
        records.append(validate({'id': digest(core), **core}, 'experience-collection-record'))
    return sorted(records, key=lambda r: r['id'])


def export_collection(runs, output, *, seed=0, ratios=(0.8, 0.1, 0.1)):
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('Collection destination must be new')
    output = output.resolve(); output.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix='.refund-collection-', dir=output.parent))
    try:
        children = []
        for run in runs:
            name = digest({'name': run['name'], 'bundle': run['bundle']})
            export_dataset(run['bundle'], run['report'], temp / 'children' / name,
                           directory=run['directory'], seed=seed)
            children.append((name, load_dataset(temp / 'children' / name)))
        records = _records(children, seed, ratios)
        splits = {s: [r['id'] for r in records if r['split'] == s] for s in SPLITS}
        if any(not ids for ids in splits.values()):
            raise ValueError('Need at least three eligible independent lineage groups for nonempty splits')
        (temp / 'records.jsonl').write_text(''.join(canonical(r) + '\n' for r in records), encoding='utf-8')
        manifest = {'schema_version': VERSION, 'seed': seed, 'ratios': list(ratios),
                    'children': sorted(name for name, _ in children), 'splits': splits,
                    'record_count': len(records), 'split_group_count': len({r['group_id'] for r in records}),
                    'split_policy': 'connected-source-family-parent-equivalent-task.v2', 'policy_rollout_ready': False,
                    'artifacts': _inventory(temp)}
        save(temp / 'manifest.json', manifest)
        validate_collection(temp)
        temp.rename(output)
        return manifest
    finally:
        if temp.exists(): shutil.rmtree(temp)


def _inventory(root):
    return [{'path': p.relative_to(root).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sorted(root.rglob('*')) if p.is_file() and p != root / 'manifest.json']


def validate_collection(directory):
    root = Path(directory).resolve()
    if any(p.is_symlink() for p in root.rglob('*')):
        raise ValueError('Collection cannot contain symlinks')
    manifest = validate(read(root / 'manifest.json', limit=100*1024*1024), 'experience-collection')
    if set(manifest) != {'schema_version','seed','ratios','children','splits','record_count','split_group_count','split_policy','policy_rollout_ready','artifacts'} or manifest['schema_version'] != VERSION:
        raise ValueError('Unsupported collection manifest')
    if manifest['artifacts'] != _inventory(root):
        raise ValueError('Collection artifact inventory mismatch')
    names = manifest['children']
    if names != sorted(set(names)) or any(len(n) != 64 or any(c not in '0123456789abcdef' for c in n) for n in names):
        raise ValueError('Unsafe or duplicate collection child')
    allowed = {'records.jsonl'} | {p.relative_to(root).as_posix() for n in names for p in (root / 'children' / n).rglob('*') if p.is_file()}
    if {a['path'] for a in manifest['artifacts']} != allowed:
        raise ValueError('Unlisted collection child or artifact')
    children = [(n, load_dataset(root / 'children' / n)) for n in names]
    records = _records(children, manifest['seed'], manifest['ratios'])
    actual = [json.loads(s) for s in (root / 'records.jsonl').read_text().splitlines()]
    if actual != records:
        raise ValueError('Collection records or global lineage assignments differ from evidence')
    splits = {s: [r['id'] for r in records if r['split'] == s] for s in SPLITS}
    if manifest['splits'] != splits or any(not x for x in splits.values()) or manifest['record_count'] != len(records) or manifest['split_group_count'] != len({r['group_id'] for r in records}):
        raise ValueError('Collection splits must be nonempty and reproducible')
    if manifest['split_policy'] != 'connected-source-family-parent-equivalent-task.v2' or manifest['policy_rollout_ready'] is not False:
        raise ValueError('Unsupported collection policy')
    return {'manifest': manifest, 'records': records, 'children': dict(children)}


def held_out(directory):
    """Pair existing broken/fixed executions strictly on validation/test scenarios.

This measures implementation changes, never claims an RL training improvement.
"""
    data = validate_collection(directory)
    result = {'schema_version': 'refund-held-out.v1', 'dataset_digest': digest(data['manifest']),
              'scope': 'synthetic registered candidates; no training performed', 'splits': {}}
    for split in ('validation', 'test'):
        pairs = {}
        for row in data['records']:
            if row['split'] == split and row['candidate']['id'] in ('broken', 'fixed'):
                pairs.setdefault(row['scenario_digest'], {}).setdefault(row['candidate']['id'], []).append(row)
        if not pairs or any(set(p) != {'broken', 'fixed'} for p in pairs.values()):
            raise ValueError('Held-out comparison requires matched baseline and candidate evidence')
        rates = {}
        for candidate in ('broken', 'fixed'):
            successes = sum(all(r['verdict'] == 'pass' for r in p[candidate]) for p in pairs.values())
            rates[candidate] = {'numerator': successes, 'denominator': len(pairs), 'value': successes / len(pairs)}
        result['splits'][split] = {'candidates': rates, 'success_rate_delta': rates['fixed']['value'] - rates['broken']['value'],
            'scenario_digests': sorted(pairs), 'result_digests': sorted({r['result_digest'] for p in pairs.values() for rows in p.values() for r in rows})}
    result['cross_model_failure_reproduction'] = {'state': 'unmeasured', 'value': None, 'reason': 'Requires at least two configured live models'}
    result['rl_training_success_improvement'] = {'state': 'unmeasured', 'value': None, 'reason': 'No policy training performed'}
    return result


def write_report(directory, path):
    import html
    value = held_out(directory)
    data = validate_collection(directory)
    counts = {k: len(v) for k, v in data['manifest']['splits'].items()}
    rows = ''.join('<tr><td>' + split + '</td><td>' + str(v['candidates']['broken']['numerator']) + '/' + str(v['candidates']['broken']['denominator']) + '</td><td>' + str(v['candidates']['fixed']['numerator']) + '/' + str(v['candidates']['fixed']['denominator']) + '</td></tr>' for split, v in value['splits'].items())
    content = ('<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
               '<title>Refund 独立家族与 held-out 评估</title><style>body{font:16px system-ui;max-width:1000px;margin:40px auto;padding:20px;background:#f5f7fa;color:#172033}td,th{padding:12px;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
               '<h1>Refund：独立任务家族与 held-out 评估</h1><p>' + str(data['manifest']['split_group_count']) + ' 个来源分组 · ' + str(data['manifest']['record_count']) + ' 条带 reward 的记录</p><p>' + html.escape(str(counts)) + '</p>'
               '<p>来源、父失败、任务家族及等价任务连接在同一划分。每条记录保留原始来源和 verifier 正反例资格证据。</p><table><tr><th>划分</th><th>Broken 成功/任务数</th><th>Fixed 成功/任务数</th></tr>' + rows + '</table>'
               '<p>上述结果来自合成环境中的规则候选，不能解释为模型训练收益。真实跨模型实测等待模型与 provider 配置。</p><details><summary>评估证据</summary><pre>' + html.escape(json.dumps(value, ensure_ascii=False, indent=2)) + '</pre></details></html>')
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content, encoding='utf-8')
    return value
