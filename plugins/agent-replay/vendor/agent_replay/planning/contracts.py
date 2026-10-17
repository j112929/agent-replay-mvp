"""Small explicit planning contracts. Hypotheses are never trajectory v2 steps."""
import copy
from dataclasses import dataclass, field, asdict
import math
import uuid
from ..serialization import canonical, digest
from ..security.sanitizer import Sanitizer, FAILED


def ident(): return str(uuid.uuid4())


def finite(value, name, low=0, high=None):
    if type(value) not in (int, float) or not math.isfinite(value) or value < low or high is not None and value > high:
        raise ValueError('Invalid ' + name)


def data(value):
    encoded = canonical(value)
    if len(encoded.encode()) > 65536: raise ValueError('Planning payload exceeds 64 KiB')
    cleaner = Sanitizer()
    result = cleaner.sanitize(copy.deepcopy(value))
    if cleaner.failed or FAILED in canonical(result): raise ValueError('Planning sanitization unavailable')
    return result


@dataclass
class PlanningConfig:
    enabled: bool = False
    candidates: int = 3
    horizon: int = 3
    rollouts_per_candidate: int = 3
    max_planning_latency_ms: float = 10000
    max_planning_tokens: int = 12000
    max_predictions: int = 128
    max_real_steps: int = 6
    fallback: str = 'abort'
    seed: int = 0
    lambda_cost: float = 1
    lambda_risk: float = 2
    lambda_uncertainty: float = .25

    def __post_init__(self):
        if type(self.enabled) is not bool or self.fallback not in ('abort', 'baseline'): raise ValueError('Invalid planning policy')
        for key, maximum in [('candidates',8),('horizon',8),('rollouts_per_candidate',8),('max_predictions',512),('max_real_steps',100),('max_planning_tokens',1000000)]:
            if type(getattr(self,key)) is not int or not 1 <= getattr(self,key) <= maximum: raise ValueError('Invalid ' + key)
        finite(self.max_planning_latency_ms,'planning deadline',1,60000)
        if type(self.seed) is not int: raise ValueError('Invalid seed')
        for key in ('lambda_cost','lambda_risk','lambda_uncertainty'): finite(getattr(self,key),key)


@dataclass
class AgentContext:
    task_id: str
    task_group: str
    goal: str
    state: dict
    state_version: int
    environment_version: str
    history: list = field(default_factory=list)
    trajectory_id: str | None = None
    benchmark_run_id: str | None = None

    def __post_init__(self):
        for key in ('task_id','task_group','goal','environment_version'):
            if not isinstance(getattr(self,key),str) or not getattr(self,key): raise ValueError('Invalid context ' + key)
        if type(self.state_version) is not int or self.state_version < 0 or not isinstance(self.state,dict): raise ValueError('Invalid state')
        self.state = data(self.state); self.history = data(self.history)

    @property
    def state_digest(self): return digest([self.environment_version,self.state_version,self.state])

    def branch(self, delta, observation):
        if not isinstance(delta,dict) or not isinstance(observation,dict): raise ValueError('Structured prediction required')
        return AgentContext(self.task_id,self.task_group,self.goal,{**self.state,**data(delta)},self.state_version+1,
            self.environment_version,(self.history+[data(observation)])[-8:],self.trajectory_id,self.benchmark_run_id)


@dataclass
class CandidateAction:
    candidate_id: str
    action_type: str
    action_payload: dict
    tool_name: str
    generation_model: str = 'existing-policy'
    generation_cost: float | None = 0
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        for key in ('candidate_id','action_type','tool_name','generation_model'):
            if not isinstance(getattr(self,key),str) or not getattr(self,key) or len(getattr(self,key)) > 120: raise ValueError('Invalid candidate ' + key)
        if self.action_type != 'tool' or not isinstance(self.action_payload,dict): raise ValueError('Only structured tool candidates supported')
        self.action_payload=data(self.action_payload); self.metadata=data(self.metadata)
        if self.generation_cost is not None: finite(self.generation_cost,'generation cost')

    @property
    def key(self): return digest([self.action_type,self.tool_name,self.action_payload])


@dataclass
class PredictionConfig:
    seed: int = 0
    max_output_tokens: int = 512
    remaining_ms: float = 10000


@dataclass
class PredictionDistribution:
    prediction_id: str
    candidate_id: str
    outcomes: list
    model_id: str
    model_version: str
    calibration_status: str = 'uncalibrated'
    prediction_latency_ms: float = 0
    prediction_token_usage: int | None = 0
    cost_usd: float | None = 0
    raw_scores: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.outcomes,list) or not 1 <= len(self.outcomes) <= 8: raise ValueError('Expected 1–8 predicted outcomes')
        self.outcomes=data(self.outcomes)
        self.metadata=data(self.metadata)
        for outcome in self.outcomes:
            if not isinstance(outcome,dict) or not isinstance(outcome.get('observation'),dict) or not isinstance(outcome.get('state_delta'),dict): raise ValueError('Invalid predicted observation/state')
            if type(outcome.get('terminal')) is not bool or outcome.get('success') is not None and type(outcome['success']) is not bool: raise ValueError('Invalid terminal prediction')
            if 'terminal' in outcome['state_delta'] and outcome['state_delta']['terminal'] != outcome['terminal']: raise ValueError('Contradictory terminal state')
            if outcome.get('success') is not None and 'success' in outcome['state_delta'] and outcome['state_delta']['success'] != outcome['success']: raise ValueError('Contradictory success prediction')
            for key in ('probability','success_probability'):
                if outcome.get(key) is not None: finite(outcome[key],key,0,1)
            finite(outcome.get('uncertainty',1),'uncertainty',0,1)
            finite(outcome.get('cost_usd',0),'execution cost')
            if outcome.get('reward') is not None: finite(outcome['reward'],'reward',-100,100)
        probabilities=[o.get('probability') for o in self.outcomes]
        if any(p is not None for p in probabilities) and (any(p is None for p in probabilities) or abs(sum(probabilities)-1)>1e-6): raise ValueError('Invalid probability distribution')
        if self.calibration_status not in ('uncalibrated','empirical_uncalibrated','unavailable','fitted_unvalidated'): raise ValueError('Unsupported calibration claim')
        finite(self.prediction_latency_ms,'prediction latency')
        if self.prediction_token_usage is not None and (type(self.prediction_token_usage) is not int or self.prediction_token_usage<0): raise ValueError('Invalid token usage')
        if self.cost_usd is not None: finite(self.cost_usd,'prediction cost')
        for key in ('prediction_id','candidate_id','model_id','model_version'):
            if not isinstance(getattr(self,key),str) or not getattr(self,key): raise ValueError('Missing model/prediction provenance')

    @classmethod
    def parse(cls, value, candidate_id):
        if not isinstance(value,dict): raise ValueError('Prediction must be an object')
        parsed=cls(**value)
        if parsed.candidate_id != candidate_id: raise ValueError('Prediction candidate mismatch')
        return parsed


@dataclass
class ImaginedTrajectory:
    imagination_id: str
    candidate_id: str
    origin_state_digest: str
    simulated_steps: list
    predicted_terminal_state: dict
    uncertainty: float
    termination_reason: str
    synthetic: bool = True


@dataclass
class TrajectoryScore:
    imagination_id: str
    candidate_id: str
    expected_task_reward: float
    expected_execution_cost: float
    predicted_failure_risk: float
    uncertainty: float
    estimated_remaining_steps: int
    estimated_success_probability: float | None
    score: float

    def __post_init__(self):
        finite(self.expected_task_reward,'reward',-100,100)
        finite(self.expected_execution_cost,'expected cost')
        finite(self.predicted_failure_risk,'failure risk',0,1)
        finite(self.uncertainty,'uncertainty',0,1)
        finite(self.score,'selection score',-1e100,1e100)
        if self.estimated_success_probability is not None: finite(self.estimated_success_probability,'success probability',0,1)

