"""Trusted adapter workers. No code imports or execution directives from the server."""
import threading
import time
from ..serialization import digest
from . import bandit


def run_once(client, role, owner, adapter=bandit, lease_seconds=60):
    job = client.claim(kind=role, owner=owner, seconds=lease_seconds)
    if job is None:
        return None
    stop = threading.Event()
    lease_errors = []

    def renew():
        while not stop.wait(lease_seconds/3):
            try:
                client.heartbeat(job=job['id'], token=job['token'], seconds=lease_seconds)
            except Exception as exc:
                lease_errors.append(exc)
                return

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    try:
        payload = job['payload']
        if role == 'actor':
            policy = client.policy(version=payload['policy_version'])
            if digest(policy['weights']) != payload['policy_digest']:
                raise ValueError('Policy artifact digest mismatch')
            result = adapter.rollout(payload, policy)
            action, args = 'complete', {'trajectory': result}
        elif role == 'verifier':
            trajectory = client.trajectory(trajectory_id=payload['trajectory_id'])
            if digest(trajectory['data']) != trajectory['digest']:
                raise ValueError('Trajectory artifact digest mismatch')
            reward, verifier = adapter.verify(trajectory['data'])
            action, args = 'verify', {'reward': reward, 'verifier': verifier}
        elif role == 'learner':
            policy = client.policy(version=payload['policy_version'])
            if digest(policy['weights']) != payload['policy_digest']:
                raise ValueError('Policy artifact digest mismatch')
            samples = [client.trajectory(trajectory_id=i) for i in payload['trajectory_ids']]
            if any(digest(s['data']) != s['digest'] for s in samples):
                raise ValueError('Trajectory artifact digest mismatch')
            weights, metrics = adapter.learn(policy, samples)
            action, args = 'publish', {'weights': weights, 'metrics': metrics}
        else:
            raise ValueError('Unknown worker role')
        if lease_errors:
            raise lease_errors[0]
        return getattr(client, action)(job=job['id'], token=job['token'], **args)
    except Exception as exc:
        try:
            # Preserve a bounded class name, not secrets in arbitrary adapter exceptions.
            client.fail(job=job['id'], token=job['token'], kind=role, error=type(exc).__name__)
        except Exception:
            pass  # Expiry fencing will recover work if the controller is unreachable.
        raise
    finally:
        stop.set()
        thread.join(timeout=2)


def run(client, role, owner, adapter=bandit, drain=False):
    while True:
        try:
            result = run_once(client, role, owner, adapter)
        except Exception:
            if drain:
                raise
            time.sleep(1)
            continue
        if result is None:
            if drain:
                return
            time.sleep(1)
