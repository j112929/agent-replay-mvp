"""Empirical upper-tail loss CVaR with fractional quantile boundary mass."""
import math
from statistics import mean, pvariance


def cvar(losses, alpha=.9):
    if type(alpha) not in (float,int) or not math.isfinite(alpha) or not 0 <= alpha < 1:
        raise ValueError('CVaR alpha must be within [0,1)')
    if not losses or any(type(x) not in (float,int) or not math.isfinite(x) for x in losses):
        raise ValueError('Finite nonempty loss sample required')
    # Uniform empirical distribution; integrate descending losses over (1-alpha).
    mass = len(losses) * (1-alpha)
    remaining, total = mass, 0.
    for loss in sorted(losses, reverse=True):
        take = min(1., remaining)
        total += take * loss
        remaining -= take
        if remaining <= 1e-12:
            break
    return total / mass


def candidate_metrics(rollouts, alpha=.9):
    if not rollouts or any(not r['complete'] for r in rollouts):
        raise ValueError('Incomplete simulation cannot support action selection')
    rewards = [sum(s['reward'] for s in r['steps']) for r in rollouts]
    terminal = all(r['terminal'] for r in rollouts)
    return {'expected_reward': mean(rewards), 'immediate_reward': mean(r['steps'][0]['reward'] for r in rollouts),
            'expected_execution_cost':mean(sum(s['cost_usd'] for s in r['steps']) for r in rollouts),
            'return_variance': pvariance(rewards), 'loss_cvar': cvar([-v for v in rewards],alpha),
            'epistemic_uncertainty': mean(r['epistemic_uncertainty'] for r in rollouts),
            'aleatoric_return_variance': pvariance(rewards),
            'success_probability': mean(r['success'] for r in rollouts) if terminal else None,
            'severe_failure_probability': mean(any(s['observation'].get('severity')=='high' for s in r['steps']) for r in rollouts),
            'sampled_rollouts': len(rollouts), 'tail_sample_mass': (1-alpha)*len(rollouts),
            'probability_status': 'monte_carlo_uncalibrated', 'loss_definition': 'negative cumulative reward',
            'uncertainty_definition': 'model-supplied epistemic heuristic; return variance describes conditional sampling variation'}


def selection_score(metrics, method, risk_weight=.5, uncertainty_weight=0):
    if method == 'greedy':
        return metrics['immediate_reward']
    if method == 'expected_value':
        return metrics['expected_reward']
    if method == 'mean_variance':
        return metrics['expected_reward']-risk_weight*metrics['return_variance']
    if method in ('cvar','uncertainty_cvar'):
        return metrics['expected_reward']-risk_weight*metrics['loss_cvar']-(uncertainty_weight*metrics['epistemic_uncertainty'] if method=='uncertainty_cvar' else 0)
    raise ValueError('Unknown planner method')
