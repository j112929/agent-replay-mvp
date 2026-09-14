"""Control-plane CLI and a multiprocess, CPU-only learning acceptance demo."""
import argparse
import importlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import uuid

from . import bandit
from .http import Client, make_server
from .store import Store
from .worker import run
from ..serialization import digest
from ..storage import save


def export_trace(record):
    from ..migrations import to_v2
    from ..schema import validate_trace
    data = record['data']
    legacy = {'schema_version': '1.0', 'id': record['id'], 'name': 'RL rollout: '+data['environment'], 'started_at': '1970-01-01T00:00:00Z', 'status': 'success', 'duration_ms': 0, 'input': {'seed': data['seed'], 'environment': data['environment']}, 'output': {'reward': record['reward']}, 'metadata': {'rollout': {k: record[k] for k in ('policy', 'digest', 'verifier', 'batch')}, 'timing': 'not_recorded'}, 'steps': [{'id': f'transition-{i}', 'name': 'environment.transition', 'kind': 'tool', 'status': 'success', 'start_ms': 0, 'duration_ms': 0, 'input': {'observation': t['observation'], 'action': t['action']}, 'output': {'next_observation': t['next_observation'], 'terminated': t['terminated'], 'truncated': t['truncated'], 'behavior_logprob': t['behavior_logprob']}} for i, t in enumerate(data['transitions'])]}
    return to_v2(validate_trace(legacy))


def demo(directory, rounds=8, actors=3, batch_size=64):
    if not 1 <= rounds <= 100 or not 1 <= actors <= 32 or not 1 <= batch_size <= 10000:
        raise ValueError('Invalid demo dimensions')
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # A separate run database makes demo reruns safe and preserves prior evidence.
    run_id = uuid.uuid4().hex[:12]
    store = Store(directory/f'{run_id}.sqlite3')
    store.initialize({'format': 'bandit-softmax.v1', 'logits': [0.0, 0.0]})
    tokens = {role: secrets.token_urlsafe(32) for role in ('admin', 'actor', 'verifier', 'learner')}
    server = make_server(store, tokens, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    client = Client(url, tokens['admin'])
    history = [{'version': 0, 'optimal_action_probability': 0.5}]

    def workers(role, count):
        processes = []
        try:
            for i in range(count):
                env = dict(os.environ, AGENT_ROLLOUT_TOKEN=tokens[role])
                processes.append(subprocess.Popen([sys.executable, '-m', 'agent_replay.rollout.cli', 'worker', '--url', url, '--role', role, '--owner', f'{role}-{i}', '--drain'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
            for process in processes:
                stdout, stderr = process.communicate(timeout=120)
                if process.returncode:
                    raise RuntimeError(f'{role} failed: {stderr[-2000:]}')
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

    try:
        for round_id in range(rounds):
            client.enqueue(count=batch_size, environment=bandit.ENVIRONMENT, seed=round_id*batch_size+100, key=f'{run_id}:{round_id}')
            workers('actor', actors)
            workers('verifier', 2)
            client.batch(size=batch_size, environment=bandit.ENVIRONMENT, verifier=bandit.VERIFIER, key=f'{run_id}:{round_id}')
            workers('learner', 1)
            policy = client.policy()
            history.append({'version': policy['version'], 'optimal_action_probability': bandit.probabilities(policy['weights'])[1]})
        snapshot = client.snapshot()
        snapshot.update({'demo': {'algorithm': 'REINFORCE', 'environment': bandit.ENVIRONMENT, 'actors': actors, 'rounds': rounds, 'batch_size': batch_size, 'transport': 'HTTP; separate worker processes', 'history': history}})
        save(directory/'snapshot.json', snapshot)
        first = snapshot['trajectories'][0]['id']
        save(directory/'trace.json', export_trace(store.trajectory(first)))
        if history[-1]['optimal_action_probability'] <= history[0]['optimal_action_probability']:
            raise RuntimeError('Learning acceptance failed: policy did not improve')
        return {'database': store.path, 'snapshot': str(directory/'snapshot.json'), 'trace': str(directory/'trace.json'), 'initial_probability': 0.5, 'final_probability': history[-1]['optimal_action_probability'], 'policy_updates': rounds, 'trajectories': snapshot['total_trajectories']}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='agent-replay rollout')
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('serve'); p.add_argument('--database', default='.replay/rollout.sqlite3'); p.add_argument('--tokens-file', required=True); p.add_argument('--host', default='127.0.0.1'); p.add_argument('--port', type=int, default=8877)
    p = commands.add_parser('tokens'); p.add_argument('--output', required=True)
    p = commands.add_parser('worker'); p.add_argument('--url', default='http://127.0.0.1:8877'); p.add_argument('--role', choices=['actor', 'verifier', 'learner'], required=True); p.add_argument('--owner', default='worker-'+uuid.uuid4().hex[:8]); p.add_argument('--adapter', default='agent_replay.rollout.bandit'); p.add_argument('--drain', action='store_true')
    p = commands.add_parser('request'); p.add_argument('action', choices=['initialize', 'enqueue', 'batch', 'snapshot', 'policy', 'trajectory']); p.add_argument('--url', default='http://127.0.0.1:8877'); p.add_argument('--json', default='{}')
    p = commands.add_parser('demo'); p.add_argument('--directory', default='.replay/rollout-demo'); p.add_argument('--rounds', type=int, default=8); p.add_argument('--actors', type=int, default=3); p.add_argument('--batch-size', type=int, default=64)
    p = commands.add_parser('inspect'); p.add_argument('--database', required=True); p.add_argument('--output')
    p = commands.add_parser('export'); p.add_argument('trajectory_id'); p.add_argument('--database', required=True); p.add_argument('--output', required=True)
    p = commands.add_parser('replay'); p.add_argument('trajectory_id'); p.add_argument('--database', required=True); p.add_argument('--policy-version', type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == 'tokens':
            data = {role: secrets.token_urlsafe(32) for role in ('admin', 'actor', 'verifier', 'learner')}
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(data, stream)
            result = {'tokens_file': str(Path(args.output).resolve())}
        elif args.command == 'serve':
            server = make_server(Store(args.database), json.loads(Path(args.tokens_file).read_text()), args.host, args.port)
            print(json.dumps({'listening': f'{args.host}:{server.server_port}'}), flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        elif args.command == 'worker':
            adapter = importlib.import_module(args.adapter)
            run(Client(args.url, os.environ['AGENT_ROLLOUT_TOKEN']), args.role, args.owner, adapter, args.drain)
            return 0
        elif args.command == 'request':
            result = Client(args.url, os.environ['AGENT_ROLLOUT_TOKEN']).call(args.action, **json.loads(args.json))
        elif args.command == 'demo':
            result = demo(args.directory, args.rounds, args.actors, args.batch_size)
        else:
            store = Store(args.database)
            if args.command == 'inspect':
                result = store.snapshot()
                if args.output:
                    save(args.output, result)
            elif args.command == 'export':
                result = export_trace(store.trajectory(args.trajectory_id))
                save(args.output, result)
            else:
                original = store.trajectory(args.trajectory_id)['data']
                policy = store.policy(original['policy_version'] if args.policy_version is None else args.policy_version)
                candidate = bandit.rollout({**original, 'policy_version': policy['version'], 'policy_digest': policy['digest']}, policy)
                result = {'scope': 'deterministic bandit adapter only', 'source_policy': original['policy_version'], 'candidate_policy': policy['version'], 'exact_match': digest(original) == digest(candidate), 'source': original, 'candidate': candidate}
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
