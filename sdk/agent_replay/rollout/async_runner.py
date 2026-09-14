"""Bounded asynchronous rollout/verification/learning with explicit policy lag."""
import json
from pathlib import Path
import secrets
import threading
import time
import uuid
from . import bandit
from .artifacts import backup
from .executors import Processes, RayPool
from .http import Client, make_server
from .metrics import benchmark_report
from .store import Store, Conflict
from ..storage import save


def run(config, directory):
    directory = Path(directory).resolve(); directory.mkdir(parents=True,exist_ok=True)
    run_id = uuid.uuid4().hex[:12]
    remote = config.get('controller_url')
    store = None if remote else Store(directory/'controller.sqlite3')
    weights = config.get('initial_weights', {'format':'bandit-softmax.v1','logits':[0.,0.]})
    if store:
        store.initialize(weights)
    adapter = config.get('adapter','agent_replay.rollout.bandit')
    lag = config.get('max_policy_lag',0)
    if lag and adapter == 'agent_replay.rollout.bandit':
        raise ValueError('Bandit adapter is on-policy only; use the GRPO adapter for stale batches')
    rounds, size = config.get('updates',4), config.get('batch_size',16)
    if type(rounds) is not int or rounds < 1 or type(size) is not int or size < 1:
        raise ValueError('Positive updates and batch size required')
    capacity = config.get('max_inflight',size*2)
    if capacity < size:
        raise ValueError('max_inflight must be at least batch_size')
    server = thread = None
    if remote:
        tokens = json.loads(Path(config['tokens_file']).read_text())
        url = remote
    else:
        tokens = {r:secrets.token_urlsafe(32) for r in ('admin','actor','verifier','learner')}
        server = make_server(store,tokens,port=0)
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
    client = Client(url,tokens['admin'])
    client.initialize(weights=weights)
    pool = None
    start_epoch, started = client.scheduler_status()['controller_time'], time.monotonic()
    initial_version = client.policy()['version']; target_version = initial_version+rounds
    try:
        if config.get('executor','process') == 'ray':
            pool = RayPool(url,tokens,adapter,actors=config.get('actors',3),verifiers=config.get('verifiers',1),address=config.get('ray_address'),actor_gpus=config.get('actor_gpus',0),learner_gpus=config.get('learner_gpus',0))
        else:
            pool = Processes(url,tokens,adapter,actors=config.get('actors',3),verifiers=config.get('verifiers',1),actor_env=config.get('actor_env'),learner_env=config.get('learner_env'))
        sequence = 0
        while client.policy()['version'] < target_version:
            if time.monotonic()-started > config.get('timeout_seconds',300):
                raise TimeoutError('Asynchronous run deadline exceeded; database retained for recovery')
            pool.healthy()
            status = client.scheduler_status(max_policy_lag=lag)
            current, active, available, learning, exhausted = (status[k] for k in ('current','active','available','learning','exhausted'))
            if exhausted:
                raise RuntimeError('Worker retry budget exhausted; inspect persisted job results')
            if active+available < capacity:
                count = min(size,capacity-active-available)
                client.enqueue(count=count,environment=config.get('environment',bandit.ENVIRONMENT),seed=config.get('seed',100)+sequence*size,key=f'{run_id}:{sequence}',task=config.get('task'))
                sequence += 1
            if not learning:
                try:
                    client.batch(size=size,environment=config.get('environment',bandit.ENVIRONMENT),verifier=config.get('verifier',bandit.VERIFIER),key=f'{run_id}:update:{current}',max_policy_lag=lag)
                except Conflict:
                    pass  # Reward workers have not completed a policy-consistent batch yet.
            time.sleep(config.get('poll_seconds',.05))
        end_epoch = client.scheduler_status()['controller_time']
        elapsed = time.monotonic()-started
        report = benchmark_report(client.metrics(since=start_epoch,until=end_epoch),elapsed,{**config,'initial_policy_version':initial_version,'final_policy_version':client.policy()['version'],'executor':config.get('executor','process')})
        save(directory/'benchmark.json',report)
        save(directory/'snapshot.json',client.snapshot())
        if store:
            backup(store,directory/f'backup-{run_id}')
        return report
    finally:
        if pool:
            pool.close()
        if server:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
