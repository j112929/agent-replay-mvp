"""v0.5 explicit preset; old PlanningConfig defaults remain compatible."""
from ..planning.contracts import PlanningConfig


def configuration(value=None):
    value=value or {};allowed={'planning','self_correction','scoring','world_model'}
    if not isinstance(value,dict) or set(value)-allowed:raise ValueError('Unknown v0.5 config section')
    world=value.get('world_model',{})
    if not isinstance(world,dict) or set(world)-{'backend','model_id'} or world.get('backend','deterministic')!='deterministic' or world.get('model_id') is not None:raise ValueError('CLI controlled runner supports deterministic backend; configure other models explicitly in the trusted SDK')
    planning=value.get('planning',{});correction=value.get('self_correction',{});scoring=value.get('scoring',{})
    maps={'candidate_count':'candidates','samples_per_candidate':'rollouts_per_candidate','max_model_calls':'max_predictions','max_latency_ms':'max_planning_latency_ms'}
    base={'enabled':False,'mode':'shadow','horizon':1,'rollouts_per_candidate':2,'max_predictions':12,'max_planning_latency_ms':5000,
        'lambda_risk':1,'lambda_uncertainty':.5,'lambda_cost':.1}
    for key,item in planning.items():base[maps.get(key,key)]=item
    if set(correction)-{'enabled','max_replans','stop_on_policy_violation','require_safe_action_validation'}:raise ValueError('Unknown correction option')
    if correction.get('require_safe_action_validation',True) is not True:raise ValueError('Safe action validation cannot be disabled')
    base.update(self_correction=correction.get('enabled',False),max_replans=correction.get('max_replans',2),stop_on_policy_violation=correction.get('stop_on_policy_violation',True))
    weights={'value_weight':'value_weight','risk_weight':'lambda_risk','uncertainty_weight':'lambda_uncertainty','cost_weight':'lambda_cost'}
    if set(scoring)-set(weights):raise ValueError('Use existing normalized reward scale; supported risk/uncertainty/cost weights only')
    for k,v in scoring.items():base[weights[k]]=v
    return PlanningConfig(**base)
