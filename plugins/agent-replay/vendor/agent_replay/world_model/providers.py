"""Optional bounded model-response retry and sampled candidate adapters."""
import asyncio
from dataclasses import replace
import time
from ..planning.core import CandidateGenerator
from ..planning.contracts import CandidateAction

class RetryingWorldModel:
    def __init__(self,model,max_retries=1):
        if type(max_retries) is not int or not 0<=max_retries<=2:raise ValueError('Use at most two prediction retries')
        self.model=model;self.max_retries=max_retries;self.model_id=model.model_id;self.version=model.version
    def token_reservation(self,c,a):return getattr(self.model,'token_reservation',lambda c,a:0)(c,a)*(self.max_retries+1)
    async def predict(self,c,a,config):
        deadline=time.monotonic()+config.remaining_ms/1000
        for attempt in range(self.max_retries+1):
            remaining=deadline-time.monotonic()
            if remaining<=0:raise ValueError('Prediction retry deadline exhausted')
            try:
                value=await asyncio.wait_for(self.model.predict(c,a,replace(config,remaining_ms=remaining*1000)),remaining)
                if attempt:
                    # Failed responses may be billed even when parsing failed; never invent their usage.
                    value.prediction_token_usage=None;value.cost_usd=None
                value.metadata={**value.metadata,'prediction_retries':attempt,'model_calls':attempt+value.metadata.get('model_calls',0)}
                return value
            except ValueError:
                if attempt==self.max_retries:raise ValueError('Structured prediction retries exhausted') from None

class SampledCandidateGenerator(CandidateGenerator):
    """Trusted async sampler (including an LLM policy) with existing schema validation."""
    def __init__(self,sampler,schemas,seed=0):super().__init__(lambda c:[],schemas);self.sampler=sampler;self.seed=seed
    async def generate(self,context,k):
        if type(k) is not int or not 1<=k<=8:raise ValueError('Candidate limit')
        result=[];seen=set()
        for i in range(min(16,k*2)):
            value=await self.sampler(context,self.seed+i)
            action=self.validate(CandidateAction(**value) if isinstance(value,dict) else value)
            if action.key not in seen:seen.add(action.key);result.append(action)
            if len(result)==k:break
        if not result:raise ValueError('No valid sampled candidates')
        return result
