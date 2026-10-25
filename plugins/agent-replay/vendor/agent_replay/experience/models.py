"""Opt-in cross-model refund experiments over a frozen held-out dataset.

Uses the existing bounded worker, provider registry, local state adapters and
business verifier. Offline wire tests are explicitly excluded from live metrics.
"""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from ..experiments import execute, resolve_spec
from ..project import load_project
from ..schema import validate_trace
from ..serialization import digest
from ..storage import read, save
from .collection import validate_collection
from .compiler import replay_spec
from .refund import registry, _retry, payment
from .verifiers import evaluate_verifier, qualification_current

VERSION = 'refund-model-evaluation.v1'


def agent(run):
    order = _retry(run.input['order_id'])
    # Only public task and observed order state are shown. No witness, qualification,
    # reference verdict or private environment is present in the model prompt.
    public = {k: v for k, v in run.input.items() if k != '_environment'}
    payload = run.chat(model='configured-model', messages=[
        {'role': 'system', 'content': 'Execute one refund. Respect structured request, approval and remaining paid balance. Return only JSON with order_id and integer amount_minor.'},
        {'role': 'user', 'content': json.dumps({'task': public, 'order': order, 'currency': run.input['_environment']['initial_state']['currency']}, ensure_ascii=False)}])
    action = json.loads(payload['choices'][0]['message']['content'])
    if not isinstance(action, dict) or set(action) != {'order_id', 'amount_minor'} or not isinstance(action['order_id'], str) or type(action['amount_minor']) is not int:
        raise ValueError('Model action must contain order_id and integer amount_minor')
    return payment(action['order_id'], action['amount_minor'])


def _validate_config(config):
    if set(config) != {'models', 'repetitions', 'scope'} or config['scope'] not in ('live', 'offline-wire-test'):
        raise ValueError('Supply models, repetitions and live/offline-wire-test scope')
    if type(config['repetitions']) is not int or not 1 <= config['repetitions'] <= 10 or not 2 <= len(config['models']) <= 5:
        raise ValueError('Use 2–5 models and 1–10 repetitions')
    identities = []
    for model in config['models']:
        if set(model) != {'model', 'version', 'provider'} or not all(isinstance(model[k], str) and model[k] for k in ('model', 'version')):
            raise ValueError('Model requires explicit model and pinned version')
        provider = model['provider']
        if set(provider) - {'kind', 'base_url', 'api_key_env'} or provider.get('kind', 'openai_compatible') not in ('openai_compatible', 'anthropic'):
            raise ValueError('Use the existing provider registry; secrets belong in environment variables')
        if 'api_key' in provider or not all(isinstance(v, str) for v in provider.values()):
            raise ValueError('Provider configuration must contain only strings and no secret')
        endpoint = urlsplit(provider.get('base_url', ''))
        if endpoint.scheme not in ('http', 'https') or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ValueError('Supply an explicit provider endpoint without credentials or query parameters')
        identity = (provider.get('kind', 'openai_compatible'), endpoint.scheme.lower(), endpoint.hostname.lower(), endpoint.port, endpoint.path.rstrip('/'))
        identities.append((identity, model['model']))
    if len(set(identities)) != len(identities):
        raise ValueError('Model aliases do not establish independent models')
    return config


def _project(model):
    project = registry()
    project['entrypoints']['model'] = {'callable': 'agent_replay.experience.models:agent'}
    project['providers'] = {'selected': model['provider']}
    return project


def _spec(entry, model):
    value = replay_spec(entry)
    value.update(entrypoint_id='model', policy='hybrid', model_rules=[
        {'selector': {'kind': 'llm'}, 'mode': 'live', 'provider_id': 'selected', 'model': model['model']}])
    value['limits'] = {'timeout_seconds': 60, 'max_steps': 20, 'max_model_calls': 1, 'max_tokens': 256}
    return value


def run_models(dataset, config, output):
    _validate_config(config)
    data = validate_collection(dataset)
    root = Path(output).resolve()
    if root.exists(): raise ValueError('Model experiment destination must be new')
    root.mkdir(parents=True)
    rows = []
    selected = {r['scenario_digest']: r for r in data['records'] if r['split'] in ('validation', 'test')}
    for scenario, row in sorted(selected.items()):
        store = data['children'][row['child']]['objects']
        entry = {k: store.get(row[k + '_digest']) for k in ('scenario', 'environment', 'verifier', 'requirement')}
        source = store.get(row['source_trace_digest'])
        qualification_current(store.get(row['qualification_digest']), entry, source, store)
        for index, model in enumerate(config['models']):
            case_root = root / scenario / str(index)
            save(case_root / 'agent-replay.json', _project(model))
            project = load_project(case_root / 'agent-replay.json')
            spec = resolve_spec(_spec(entry, model), project)
            for repetition in range(config['repetitions']):
                experiment = execute(source, spec, project, case_root / str(repetition))
                path = Path('executions') / (experiment['id'] + '.json')
                save(root / path, experiment)
                rows.append({'scenario_digest': scenario, 'split': row['split'], 'model_index': index,
                             'repetition': repetition, 'experiment_path': path.as_posix(), 'experiment_digest': digest(experiment)})
    report = {'schema_version': VERSION, 'dataset_digest': digest(data['manifest']), 'config': config,
              'agent_digest': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'runs': rows}
    save(root / 'report.json', report)
    metrics = evaluate_models(dataset, root)
    save(root / 'evaluation.json', metrics)
    return metrics


def evaluate_models(dataset, directory):
    data = validate_collection(dataset); root = Path(directory).resolve()
    report = read(root / 'report.json', limit=100*1024*1024)
    if set(report) != {'schema_version','dataset_digest','config','agent_digest','runs'} or report['schema_version'] != VERSION or report['dataset_digest'] != digest(data['manifest']):
        raise ValueError('Model report dataset binding mismatch')
    config = _validate_config(report['config'])
    if report['agent_digest'] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        raise ValueError('Model agent implementation changed')
    selected = {r['scenario_digest']: r for r in data['records'] if r['split'] in ('validation', 'test')}
    expected = {(s, i, n) for s in selected for i in range(len(config['models'])) for n in range(config['repetitions'])}
    seen, experiments, decisions = set(), set(), {}
    statuses = {'pass': 0, 'fail': 0, 'error': 0, 'inconclusive': 0}
    for row in report['runs']:
        key = (row['scenario_digest'], row['model_index'], row['repetition'])
        if key not in expected or key in seen or row['split'] != selected[key[0]]['split']:
            raise ValueError('Duplicate, train or unexpected model run')
        seen.add(key)
        path = root / row['experiment_path']
        if not path.resolve().is_relative_to(root) or path.is_symlink(): raise ValueError('Unsafe experiment path')
        experiment = read(path, limit=100*1024*1024)
        if digest(experiment) != row['experiment_digest'] or experiment['id'] in experiments:
            raise ValueError('Corrupt or reused model experiment')
        experiments.add(experiment['id'])
        original = selected[key[0]]; store = data['children'][original['child']]['objects']
        entry = {k: store.get(original[k + '_digest']) for k in ('scenario', 'environment', 'verifier', 'requirement')}
        source = store.get(original['source_trace_digest']); model = config['models'][key[1]]
        project = {**_project(model), '_root': str(root)}
        spec = resolve_spec(_spec(entry, model), project)
        if experiment['spec'] != spec or experiment['spec_id'] != digest(spec) or experiment['source_digest'] != digest(source):
            raise ValueError('Model experiment spec or source binding mismatch')
        verdict = 'error'
        trace = experiment.get('trace')
        if trace is not None:
            validate_trace(trace)
            if trace['replay']['spec_id'] != digest(spec) or trace['replay']['source_digest'] != digest(source) or trace['input']['value'] != spec['input_override']:
                raise ValueError('Model trace replay binding mismatch')
            llms = [s for s in trace['steps'] if s['kind'] == 'llm']
            actual = len(llms) == 1 and llms[0].get('replay_mode') == 'live' and llms[0].get('request', {}).get('model') == model['model'] and isinstance(llms[0].get('response'), dict)
            if actual and trace['fidelity']['level'] == 'live_variant' and trace['fidelity']['model_calls'] == 1:
                # Provider/transport failures must never count as business failures.
                if (trace['execution'].get('error') or {}).get('type') == 'ProviderError': verdict = 'error'
                else: verdict = evaluate_verifier(trace, entry['verifier'], entry['environment'])['verdict']
            else:
                verdict = 'error' if (trace['execution'].get('error') or {}).get('type') == 'ProviderError' else 'inconclusive'
        statuses[verdict] += 1; decisions[key] = verdict
    if seen != expected: raise ValueError('Model report omitted planned held-out executions')
    per_split = {}
    for split in ('validation', 'test'):
        per_split[split] = []
        for index, model in enumerate(config['models']):
            verdicts = [v for (s, i, n), v in decisions.items() if i == index and selected[s]['split'] == split]
            decisive = sum(v in ('pass', 'fail') for v in verdicts)
            per_split[split].append({'model': model['model'], 'version': model['version'], 'planned': len(verdicts),
                'successes': verdicts.count('pass'), 'decisive': decisive,
                'success_rate': verdicts.count('pass') / decisive if decisive else None})
    common = [s for s in selected if all(all(decisions[s, i, n] in ('pass','fail') for n in range(config['repetitions'])) for i in range(len(config['models'])))]
    reproduced = [s for s in common if sum(all(decisions[s, i, n] == 'fail' for n in range(config['repetitions'])) for i in range(len(config['models']))) >= 2]
    measured = config['scope'] == 'live' and bool(common)
    return {'schema_version': VERSION, 'dataset_digest': report['dataset_digest'], 'report_digest': digest(report),
            'scope': config['scope'], 'verdict_counts': statuses, 'held_out': per_split,
            'cross_model_failure_reproduction': {'state': 'measured' if measured else 'unmeasured',
                'numerator': len(reproduced) if measured else 0, 'denominator': len(common) if measured else 0,
                'value': len(reproduced) / len(common) if measured else None,
                'scenario_digests': reproduced if measured else []},
            'coverage': {'planned_runs': len(expected), 'decisive_runs': statuses['pass'] + statuses['fail'], 'common_decisive_scenarios': len(common)},
            'notes': ['Pinned version is caller supplied; retain actual provider responses in experiments.',
                      'Offline wire tests are excluded from cross-model measurements. No RL training performed.']}
