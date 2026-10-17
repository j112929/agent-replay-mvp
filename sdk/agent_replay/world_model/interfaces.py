import asyncio
from dataclasses import dataclass, field, replace
from ..planning.contracts import AgentContext, CandidateAction, PredictionConfig

class UnsupportedPrediction(ValueError):
    def __init__(self,code):
        if code not in ('environment_mismatch','missing_context','unsupported_prediction'):code='unsupported_prediction'
        self.code=code;super().__init__(code)

@dataclass
class PredictionRequest:
    context: AgentContext
    action: CandidateAction
    config: PredictionConfig = field(default_factory=PredictionConfig)

class WorldModelAdapter:
    """Compatible convenience methods over existing WorldModel.predict, no new runtime."""
    def __init__(self, model): self.model=model
    async def predict_next(self, history, action, task):
        context=AgentContext(**{**task,'history':history}) if isinstance(task,dict) else replace(task,history=history)
        return await self.model.predict(context,action,PredictionConfig())
    async def predict_batch(self, requests):
        if not isinstance(requests,list) or len(requests)>32: raise ValueError('Prediction batch limit is 32')
        # Sequential: a batch cannot silently multiply concurrency/spend.
        return [await asyncio.wait_for(self.model.predict(r.context,r.action,r.config),r.config.remaining_ms/1000) for r in requests]
