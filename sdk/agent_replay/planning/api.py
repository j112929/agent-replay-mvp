"""Bounded, mock-only simulation shared by authenticated API surfaces."""
import asyncio
from dataclasses import asdict
from .contracts import AgentContext, PlanningConfig
from .core import CandidateGenerator, Planner
from .models import MockWorldModel
from .benchmark import SCHEMAS, policy
from .store import PlanningStore


def simulate(body, root, sensitive_fields=()):
    if not isinstance(body,dict) or set(body)-{'context','config','variant'}: raise ValueError('Unsupported simulation request')
    from ..security.sanitizer import Sanitizer, FAILED
    from ..serialization import canonical
    cleaner=Sanitizer(sensitive_fields=sensitive_fields)
    body=cleaner.sanitize(body)
    if cleaner.failed or FAILED in canonical(body): raise ValueError('Planning sanitization unavailable')
    config=PlanningConfig(**{'max_predictions':32,**body.get('config',{})})
    if not config.enabled: raise ValueError('Planning feature must be explicitly enabled')
    if config.max_predictions>32 or config.horizon>3 or config.rollouts_per_candidate>3 or config.max_planning_latency_ms>10000: raise ValueError('Hosted simulation budget exceeded; use the trusted SDK offline')
    context=AgentContext(**body['context'])
    model=MockWorldModel(body.get('variant','tests-only'))
    session=asyncio.run(Planner(CandidateGenerator(policy,SCHEMAS),model,config).plan(context))
    PlanningStore(root).put(session)
    return session
