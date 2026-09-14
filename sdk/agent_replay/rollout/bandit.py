"""A real REINFORCE learner for a two-action bandit, not a simulated LLM trainer."""
import math
import random

ENVIRONMENT = 'two-arm-bandit.v1'
VERIFIER = 'bandit-reward.v1'


def probabilities(weights):
    logits = weights.get('logits')
    if weights.get('format') != 'bandit-softmax.v1' or not isinstance(logits, list) or len(logits) != 2 or any(type(x) not in (int, float) or not math.isfinite(x) for x in logits):
        raise ValueError('Expected two finite bandit logits')
    values = [math.exp(x-max(logits)) for x in logits]
    return [x/sum(values) for x in values]


def rollout(payload, policy):
    if payload['environment'] != ENVIRONMENT:
        raise ValueError('Unsupported environment')
    p = probabilities(policy['weights'])
    action = int(random.Random(payload['seed']).random() >= p[0])
    return {'schema_version': 'rollout.v1', **{k: payload[k] for k in ('policy_version', 'policy_digest', 'environment', 'seed')}, 'transitions': [{'observation': {'context': 'choose-arm'}, 'action': action, 'next_observation': None, 'behavior_logprob': math.log(p[action]), 'terminated': True, 'truncated': False}]}


def verify(trajectory):
    if trajectory['environment'] != ENVIRONMENT or len(trajectory['transitions']) != 1:
        raise ValueError('Unsupported bandit episode')
    action = trajectory['transitions'][0]['action']
    if type(action) is not int or action not in (0, 1):
        raise ValueError('Invalid bandit action')
    return float(action == 1), VERIFIER


def learn(policy, samples, learning_rate=0.5):
    p = probabilities(policy['weights'])
    gradient = [0.0, 0.0]
    rewards = []
    for sample in samples:
        trajectory = sample['data']
        if sample['verifier'] != VERIFIER or trajectory['policy_digest'] != policy['digest']:
            raise ValueError('Batch provenance mismatch')
        action = trajectory['transitions'][0]['action']
        expected_reward, _ = verify(trajectory)
        if sample['reward'] != expected_reward or not math.isclose(trajectory['transitions'][0]['behavior_logprob'], math.log(p[action]), abs_tol=1e-12):
            raise ValueError('Behavior policy or reward evidence mismatch')
        rewards.append(sample['reward'])
        for k in range(2):
            gradient[k] += sample['reward']*((1 if action == k else 0)-p[k])/len(samples)
    if not samples:
        raise ValueError('Empty batch')
    weights = {'format': 'bandit-softmax.v1', 'logits': [x+learning_rate*g for x, g in zip(policy['weights']['logits'], gradient)]}
    return weights, {'mean_reward': sum(rewards)/len(rewards), 'optimal_action_probability': probabilities(weights)[1], 'gradient_norm': math.sqrt(sum(g*g for g in gradient)), 'samples': len(samples)}
