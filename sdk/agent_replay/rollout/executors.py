"""Persistent process or Ray workers sharing the same lease protocol."""
import importlib
import os
from pathlib import Path
import subprocess
import sys
import threading
from .http import Client
from .worker import run


class Processes:
    def __init__(self, url, tokens, adapter, actors=3, verifiers=1, actor_env=None, learner_env=None):
        self.processes = []
        for role, count in [('actor',actors),('verifier',verifiers),('learner',1)]:
            for i in range(count):
                env = dict(os.environ, AGENT_ROLLOUT_TOKEN=tokens[role])
                env.update((actor_env if role=='actor' else learner_env if role=='learner' else {}) or {})
                env['PYTHONPATH'] = os.pathsep.join(filter(None,[str(Path(__file__).resolve().parents[2]),env.get('PYTHONPATH')]))
                self.processes.append(subprocess.Popen([sys.executable,'-m','agent_replay.rollout.cli','worker','--url',url,'--role',role,'--owner',f'{role}-{i}','--adapter',adapter], env=env))

    def healthy(self):
        if any(p.poll() is not None for p in self.processes):
            raise RuntimeError('A persistent worker exited; inspect its error and checkpoint')

    def close(self):
        for p in self.processes:
            if p.poll() is None:
                p.terminate()
        for p in self.processes:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait()


class RayWorker:
    def __init__(self, url, token, role, owner, adapter):
        self.stop = threading.Event()
        self.thread = threading.Thread(target=run, args=(Client(url,token),role,owner,importlib.import_module(adapter)), kwargs={'stop_event':self.stop}, daemon=True)
        self.thread.start()

    def healthy(self):
        return self.thread.is_alive()

    def close(self):
        self.stop.set(); self.thread.join(timeout=10)


class RayPool:
    def __init__(self, url, tokens, adapter, actors=3, verifiers=1, address=None, actor_gpus=0, learner_gpus=0):
        import ray
        self.ray = ray
        self.owns_runtime = not ray.is_initialized()
        if self.owns_runtime:
            ray.init(address=address)
        resources = ray.cluster_resources()
        if resources.get('GPU',0) < actors*actor_gpus+learner_gpus or resources.get('CPU',0) < actors+verifiers+1:
            if self.owns_runtime:
                ray.shutdown()
            raise ValueError('Ray cluster does not have enough reserved CPU/GPU resources')
        actor_type = ray.remote(RayWorker)
        self.workers = []
        for role,count,gpus in [('actor',actors,actor_gpus),('verifier',verifiers,0),('learner',1,learner_gpus)]:
            for i in range(count):
                self.workers.append(actor_type.options(num_cpus=1,num_gpus=gpus,max_restarts=2,max_task_retries=2).remote(url,tokens[role],role,f'ray-{role}-{i}',adapter))
        try:
            self.healthy()
        except Exception:
            self.close()
            raise

    def healthy(self):
        if not all(self.ray.get([w.healthy.remote() for w in self.workers],timeout=120)):
            raise RuntimeError('A Ray worker thread exited')

    def close(self):
        try:
            self.ray.get([w.close.remote() for w in self.workers],timeout=15)
        finally:
            for worker in self.workers:
                self.ray.kill(worker)
            if self.owns_runtime:
                self.ray.shutdown()
