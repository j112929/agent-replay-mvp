"""Trusted local configuration for vLLM generation and torchrun GRPO learning."""
import json
import os
from pathlib import Path
import subprocess
import sys
import socket
import signal
import tempfile
from .artifacts import verify_checkpoint
from ..serialization import digest

ENVIRONMENT = 'llm-exact-answer.v1'
VERIFIER = 'exact-answer.v1'


def config():
    value = json.loads(Path(os.environ['ROLLOUT_LLM_CONFIG']).read_text())
    if not Path(value['checkpoint_root']).is_absolute():
        raise ValueError('checkpoint_root must be an absolute shared filesystem path')
    if value.get('temperature', 1.0) != 1.0 or value.get('top_p', 1.0) != 1.0:
        raise ValueError('This learner requires unwarped temperature=1, top_p=1 behavior probabilities')
    return value


def _subprocess(module, request, processes=1):
    settings = config()
    root = Path(settings['checkpoint_root']); root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.job-', dir=root) as tmp:
        inp, out = Path(tmp)/'input.json', Path(tmp)/'output.json'
        inp.write_text(json.dumps(request))
        prefix = [sys.executable, '-m', module]
        if processes > 1:
            with socket.socket() as listener:
                listener.bind(('127.0.0.1',0))
                port = listener.getsockname()[1]
            prefix = [sys.executable, '-m', 'torch.distributed.run', '--master-addr=127.0.0.1', '--master-port='+str(port), '--nproc_per_node', str(processes), '-m', module]
        env = dict(os.environ)
        env['PYTHONPATH'] = os.pathsep.join(filter(None, [str(Path(__file__).resolve().parents[2]), env.get('PYTHONPATH')]))
        process = subprocess.Popen(prefix+[str(inp), str(out)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=settings.get('timeout_seconds', 1800))
        except BaseException:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
        if process.returncode:
            raise RuntimeError('Backend process failed: '+stderr[-2000:])
        return json.loads(out.read_text())


def rollout(payload, policy):
    if payload['environment'] != ENVIRONMENT:
        raise ValueError('Unsupported LLM environment')
    verify_checkpoint(policy['weights'], config()['checkpoint_root'])
    return _subprocess('agent_replay.rollout.vllm_worker', {'payload': payload, 'policy': policy, 'config': config()})


def group_rewards(trajectory):
    expected = trajectory['task']['expected_answer'].strip()
    generations = trajectory['generations']
    if len(generations) < 2:
        raise ValueError('GRPO requires at least two completions per prompt')
    return [float(g['text'].strip() == expected) for g in generations]


def verify(trajectory):
    if trajectory['environment'] != ENVIRONMENT:
        raise ValueError('Unsupported LLM environment')
    rewards = group_rewards(trajectory)
    return sum(rewards)/len(rewards), VERIFIER


def learn(policy, samples):
    settings = config()
    verify_checkpoint(policy['weights'], settings['checkpoint_root'])
    for sample in samples:
        if sample['verifier'] != VERIFIER or abs(verify(sample['data'])[0]-sample['reward']) > 1e-12:
            raise ValueError('Reward evidence mismatch')
    result = _subprocess('agent_replay.rollout.torch_learner', {'policy': policy, 'samples': samples, 'config': settings}, settings.get('learner_processes', 1))
    verify_checkpoint(result['weights'], settings['checkpoint_root'])
    return result['weights'], result['metrics']
