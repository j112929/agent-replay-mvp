from typing import Protocol
from .contracts import AgentContext, CandidateAction, PredictionConfig, PredictionDistribution


class CandidateActionGenerator(Protocol):
    async def generate(self, context: AgentContext, k: int) -> list[CandidateAction]: ...


class WorldModel(Protocol):
    async def predict(self, context: AgentContext, action: CandidateAction, config: PredictionConfig) -> PredictionDistribution: ...


class PlanningActionSelector(Protocol):
    async def select(self, candidates, imagined_rollouts, scores) -> dict: ...


class ImaginationEngine(Protocol):
    async def rollout(self,context: AgentContext,initial_action: CandidateAction,horizon: int,num_samples: int) -> list: ...


class PredictionEvaluator(Protocol):
    async def evaluate(self, prediction: PredictionDistribution, actual: dict) -> dict: ...


class ExecutionBoundary(Protocol):
    """Caller-owned authority; never given to the world model."""
    def snapshot(self) -> dict: ...
    def authorize(self, action: CandidateAction, expected_version: int) -> bool: ...
    def execute_action(self, action: CandidateAction) -> dict: ...
