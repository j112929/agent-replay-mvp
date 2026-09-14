"""Append-only measurements, explicit denominators and missing-data reporting."""
import json
import math
import os
import platform
import importlib.metadata
from pathlib import Path
from .. import __version__
from ..serialization import digest
import shutil
import subprocess
import threading
import time
import uuid
from ..serialization import canonical

METRICS = {'busy_seconds', 'idle_seconds', 'claim_seconds', 'rollouts', 'generated_tokens', 'tokenized_rollouts', 'training_step_seconds', 'job_seconds', 'gpu_utilization_percent', 'worker_errors', 'consumed_rollouts'}


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    return values[max(0, math.ceil(fraction*len(values))-1)]


class MetricsMixin:
    def _metric(self, db, role, name, value, event_id=None):
        db.execute('INSERT OR IGNORE INTO measurements VALUES(?,?,?,?,?)', (event_id or uuid.uuid4().hex, role, name, value, self.clock()))

    def measure(self, role, name, value, event_id):
        if role not in ('actor', 'verifier', 'learner', 'controller') or name not in METRICS or type(value) not in (float, int) or not math.isfinite(value) or value < 0 or not isinstance(event_id, str) or not event_id:
            raise ValueError('Invalid measurement')
        with self.transaction() as db:
            old = db.execute('SELECT role,name,value FROM measurements WHERE id=?', (event_id,)).fetchone()
            if old and (old['role'], old['name'], old['value']) != (role, name, value):
                raise ValueError('Measurement key reused')
            self._metric(db, role, name, value, event_id)
        return {'accepted': True}

    def metrics(self, since=0, until=None):
        with self.transaction() as db:
            rows = db.execute('SELECT role,name,value FROM measurements WHERE recorded>=? AND recorded<=?', (since, self.clock() if until is None else until)).fetchall()
        result = {}
        for row in rows:
            result.setdefault(row['role'], {}).setdefault(row['name'], []).append(row['value'])
        return result


def benchmark_report(metrics, elapsed, config):
    if elapsed <= 0:
        raise ValueError('Positive benchmark duration required')
    actors = metrics.get('actor', {})
    steps = metrics.get('learner', {}).get('training_step_seconds', [])
    gpus = [x for role in metrics.values() for x in role.get('gpu_utilization_percent', [])]
    def idle(role):
        values = metrics.get(role, {})
        busy = sum(values.get('busy_seconds', [])); idle_time = sum(values.get('idle_seconds', []))
        return {'seconds': idle_time, 'worker_observed_seconds': busy+idle_time, 'fraction': idle_time/(busy+idle_time) if busy+idle_time else None}
    versions = {}
    for package in ('torch','ray','transformers','vllm'):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    source_digest = digest({p.name:p.read_text() for p in sorted(Path(__file__).parent.glob('*.py'))})
    return {'schema_version': 'rollout-benchmark.v1', 'software':{'agent_replay':__version__,'dependencies':versions,'rollout_source_digest':source_digest}, 'config': config, 'hardware': platform.platform(), 'measurement_window_seconds': elapsed, 'warmup_excluded': False, 'gpu_utilization': {'mean_percent': sum(gpus)/len(gpus) if gpus else None, 'samples': len(gpus), 'reason': None if gpus else 'No GPU samples reported'}, 'tokens_per_second': sum(actors.get('generated_tokens', []))/elapsed if actors.get('tokenized_rollouts') else None, 'generated_tokens': sum(actors.get('generated_tokens', [])) if actors.get('tokenized_rollouts') else None, 'rollout_throughput_per_second': sum(actors.get('rollouts', []))/elapsed, 'completed_rollouts': sum(actors.get('rollouts', [])), 'learner_consumed_rollouts':sum(metrics.get('learner',{}).get('consumed_rollouts',[])), 'learner_idle': idle('learner'), 'actor_idle': idle('actor'), 'end_to_end_training_step_latency_seconds': {'count': len(steps), 'mean': sum(steps)/len(steps) if steps else None, 'p50': percentile(steps,.5), 'p95': percentile(steps,.95), 'max': max(steps) if steps else None}, 'definitions': {'tokens_per_second': 'Committed completion tokens / full wall-clock measurement window; null for non-token tasks', 'rollout_throughput': 'Committed trajectory jobs / full wall-clock window; a GRPO job contains one prompt group', 'idle': 'Reported worker waiting seconds / (waiting + claimed-job busy seconds); partial intervals from crashed workers are unavailable', 'step_latency': 'Controller clock: earliest enqueue among batch samples to policy publication, including queue, rollout, verification and learning', 'gpu': 'Mean nvidia-smi utilization.gpu samples across reporting workers/devices; sampled activity, not memory utilization or achieved FLOPS'}}


def prometheus(metrics):
    lines = []
    for role, names in sorted(metrics.items()):
        for name, values in sorted(names.items()):
            if name.endswith('seconds'):
                prefix = 'replay_'+name
                for bound in (.01,.1,1,5,10,30,60,300,3600):
                    lines.append(f'{prefix}_bucket{{role="{role}",le="{bound}"}} {sum(v<=bound for v in values)}')
                lines.append(f'{prefix}_bucket{{role="{role}",le="+Inf"}} {len(values)}')
                lines.append(f'{prefix}_sum{{role="{role}"}} {sum(values)}')
                lines.append(f'{prefix}_count{{role="{role}"}} {len(values)}')
            else:
                suffix = '_mean' if name == 'gpu_utilization_percent' else '_total'
                value = sum(values)/len(values) if suffix == '_mean' else sum(values)
                lines.append(f'replay_{name}{suffix}{{role="{role}"}} {value}')
    return '\n'.join(lines)+'\n'


def emit(client, role, name, value):
    try:
        client.measure(role=role, name=name, value=value, event_id=uuid.uuid4().hex)
    except Exception:
        pass  # Telemetry failure must not invalidate a completed learning update.


class GPUSampler:
    def __init__(self, client, role, interval=1):
        self.client, self.role, self.interval = client, role, interval
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        if self.role in ('actor','learner') and os.environ.get('CUDA_VISIBLE_DEVICES') != '' and shutil.which('nvidia-smi'):
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
        return self

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                command = ['nvidia-smi','--query-gpu=utilization.gpu','--format=csv,noheader,nounits']
                if os.environ.get('CUDA_VISIBLE_DEVICES'):
                    command.append('--id='+os.environ['CUDA_VISIBLE_DEVICES'])
                output = subprocess.check_output(command, text=True, timeout=5)
                for row in output.splitlines():
                    value = float(row.strip())
                    if 0 <= value <= 100:
                        emit(self.client, self.role, 'gpu_utilization_percent', value)
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            self.stop_event.wait(self.interval)

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=6)
