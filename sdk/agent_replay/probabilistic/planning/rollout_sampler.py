"""Bounded research imagination; has no execution-boundary capability."""
import asyncio
import copy
from dataclasses import dataclass, asdict
import random
import time
from ...planning.contracts import PredictionConfig, PredictionDistribution, finite, ident
from ...planning.core import sample_prediction, PlanningFailure
from ...serialization import canonical


@dataclass
class SamplingConfig:
    candidates: int = 4
    samples: int = 16
    horizon: int = 3
    seed: int = 0
    max_calls: int = 8192
    max_latency_ms: int = 30000
    max_tokens: int = 100000

    def __post_init__(self):
        for name, high in (('candidates',8),('samples',32),('horizon',32),('max_calls',8192),('max_latency_ms',60000),('max_tokens',1000000)):
            if type(getattr(self,name)) is not int or not 1 <= getattr(self,name) <= high:
                raise ValueError('Invalid research budget: '+name)
        if type(self.seed) is not int or not 0 <= self.seed <= 2**31-1:
            raise ValueError('Invalid research seed')
        if self.candidates*self.samples*self.horizon > self.max_calls:
            raise ValueError('Requested rollout grid exceeds call budget')


class ResearchBudget:
    """Shared across all benchmark tasks/planners in one bounded experiment."""
    def __init__(self,max_predictions=1000000,max_seconds=1800,max_bytes=1024*1024*1024):
        self.maximum,self.seconds,self.bytes=max_predictions,max_seconds,max_bytes
        self.predictions=0;self.artifact_bytes=0;self.started=time.monotonic();self.exhausted=False
    def consume(self,size=0):
        if self.predictions>=self.maximum or time.monotonic()-self.started>self.seconds or self.artifact_bytes+size>self.bytes:
            self.exhausted=True;raise PlanningFailure('Experiment resource budget exhausted')
        if size:self.artifact_bytes+=size
        else:self.predictions+=1


class SamplingFailure(PlanningFailure):
    def __init__(self,budget):
        self.budget=copy.deepcopy(budget)
        self.budget['known_cost_lower_bound_usd']=self.budget.get('cost_usd')
        self.budget.update(cost_usd=None,usage_complete=False)
        super().__init__('Bounded research prediction unavailable')


class MonteCarloSampler:
    def __init__(self, generator, world_model, config, budget=None):
        self.generator, self.model, self.config = generator, world_model, config
        self.shared_budget=budget

    async def sample(self, context, candidates):
        self.last_budget={'predictions':0,'model_calls':0,'tokens':0,'reserved_tokens':0,'cost_usd':0,'usage_complete':True}
        try:return await self._sample(context,candidates)
        except Exception:
            raise SamplingFailure(self.last_budget) from None

    async def _sample(self,context,candidates):
        if not candidates or len(candidates)>self.config.candidates:
            raise PlanningFailure('Invalid research candidate count')
        start = time.monotonic()
        deadline = start+self.config.max_latency_ms/1000
        budget = self.last_budget
        async def bounded(call):
            remaining=deadline-time.monotonic()
            if remaining<=0:
                if hasattr(call,'close'): call.close()
                raise PlanningFailure('Research deadline exhausted')
            value=await asyncio.wait_for(call,remaining)
            if time.monotonic()>deadline: raise PlanningFailure('Research deadline exhausted')
            return value
        rollouts, roots = [], {}
        for candidate in candidates:
            self.generator.validate(candidate)
            for sample in range(self.config.samples):
                # Same random stream across planner methods, independent of ordering.
                from ...serialization import digest
                offset=int(digest([context.task_id, candidate.key, sample])[:8],16)
                rng=random.Random(self.config.seed+offset)
                branch,current=copy.deepcopy(context),copy.deepcopy(candidate)
                steps=[];uncertainty=0;terminal=False;success=None
                for depth in range(self.config.horizon):
                    if budget['predictions']>=self.config.max_calls:
                        raise PlanningFailure('Research call budget exhausted')
                    reserve=getattr(self.model,'token_reservation',lambda c,a:0)(branch,current)
                    if type(reserve) is not int or reserve<0 or budget['tokens']+budget['reserved_tokens']+reserve>self.config.max_tokens:
                        raise PlanningFailure('Research token reservation exhausted')
                    budget['reserved_tokens']+=reserve;budget['predictions']+=1
                    if self.shared_budget:self.shared_budget.consume()
                    prediction=await bounded(self.model.predict(copy.deepcopy(branch),copy.deepcopy(current),
                        PredictionConfig(rng.randrange(2**31),512,max(1,(deadline-time.monotonic())*1000))))
                    prediction=PredictionDistribution.parse(asdict(prediction),current.candidate_id)
                    if any(o.get('probability') is None for o in prediction.outcomes):
                        raise PlanningFailure('Probabilistic sampling requires explicit outcome weights')
                    calls=prediction.metadata.get('model_calls',0)
                    if type(calls) is not int or calls<0: raise PlanningFailure('Invalid prediction call usage')
                    budget['model_calls']+=calls
                    if prediction.prediction_token_usage is None: budget['usage_complete']=False
                    else:
                        budget['tokens']+=prediction.prediction_token_usage;budget['reserved_tokens']-=reserve
                    if prediction.cost_usd is None: budget['cost_usd']=None
                    elif budget['cost_usd'] is not None: budget['cost_usd']+=prediction.cost_usd
                    if budget['tokens']+budget['reserved_tokens']>self.config.max_tokens: raise PlanningFailure('Research token budget exhausted')
                    if depth==0: roots.setdefault(candidate.candidate_id,asdict(prediction))
                    chosen=sample_prediction(prediction,rng)
                    uncertainty=1-(1-uncertainty)*(1-chosen.get('uncertainty',1))
                    steps.append({'depth':depth,'prediction_id':prediction.prediction_id,'action':asdict(current),
                        'observation':chosen['observation'],'state_delta':chosen['state_delta'],'reward':chosen.get('reward',0),
                        'cost_usd':chosen.get('cost_usd',0),'terminal':chosen['terminal'],'success':chosen.get('success'),
                        'simulated':True})
                    branch=branch.branch(chosen['state_delta'],chosen['observation'],current)
                    terminal,success=chosen['terminal'],chosen.get('success')
                    if terminal:break
                    if depth+1<self.config.horizon:
                        current=(await bounded(self.generator.generate(branch,1)))[0]
                rollouts.append({'imagination_id':ident(),'candidate_id':candidate.candidate_id,'origin_state_digest':context.state_digest,
                    'steps':steps,'final_state':branch.state,'terminal':terminal,'success':success,'complete':True,
                    'epistemic_uncertainty':uncertainty,'termination_reason':'terminal' if terminal else 'horizon',
                    'execution_kind':'MODEL_PREDICTED','sample_index':sample})
        # Bound sidecars independently; never relax interactive trajectory limits.
        if len(canonical(rollouts).encode())>8*1024*1024:
            raise PlanningFailure('Research sidecar size budget exceeded')
        return {'rollouts':rollouts,'root_predictions':roots,'budget':budget,'latency_ms':(time.monotonic()-start)*1000}
