"""Portable benchmark contracts; trajectories retain the existing v2 schema."""
from dataclasses import asdict, dataclass, field
import math
import re
from ..serialization import canonical

FAILURES = frozenset(('wrong_answer', 'tool_error', 'tool_loop', 'timeout',
    'environment_error', 'policy_violation', 'agent_error', 'verifier_error', 'unknown'))
INFRA_FAILURES = frozenset(('environment_error', 'verifier_error'))


def number(value, name, nullable=False):
    if nullable and value is None:
        return
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f'{name} must be finite and nonnegative')


@dataclass
class BenchmarkTask:
    id: str
    instruction: str
    metadata: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    timeout_seconds: float | None = 60

    def __post_init__(self):
        if not isinstance(self.id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}', self.id):
            raise ValueError('Invalid task ID')
        if not isinstance(self.instruction, str) or not self.instruction.strip():
            raise ValueError('Task instruction is required')
        if not isinstance(self.tags, list) or any(not isinstance(t, str) or not t for t in self.tags) or len(set(self.tags)) != len(self.tags):
            raise ValueError('Tags must be unique nonempty strings')
        if not isinstance(self.metadata, dict):
            raise ValueError('Task metadata must be an object')
        number(self.timeout_seconds, 'timeout_seconds', True)
        if self.timeout_seconds == 0:
            raise ValueError('Timeout must be positive or null')
        canonical(asdict(self))


@dataclass
class AgentConfig:
    name: str = 'demo'
    model: str | None = None
    parameters: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name:
            raise ValueError('Agent name is required')
        if not isinstance(self.parameters, dict):
            raise ValueError('Agent parameters must be an object')
        canonical(asdict(self))


@dataclass
class BenchmarkResult:
    task_id: str
    success: bool
    score: float | None = None
    duration_ms: float = 0
    steps: int = 0
    tokens: int | None = None
    cost_usd: float | None = None
    failure_category: str | None = None
    metadata: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    trajectory_id: str | None = None
    trajectory_path: str | None = None
    tool_call_count: int = 0
    model_call_count: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    retry_count: int = 0

    def __post_init__(self):
        BenchmarkTask(self.task_id, 'result', tags=self.tags)
        if type(self.success) is not bool:
            raise ValueError('success must be a boolean')
        if self.success and self.failure_category is not None:
            raise ValueError('Successful result cannot have a failure category')
        if not self.success and self.failure_category is None:
            self.failure_category = 'unknown'
        if self.failure_category is not None and self.failure_category not in FAILURES:
            raise ValueError('Unknown failure category')
        for name in ('duration_ms', 'steps', 'tool_call_count', 'model_call_count', 'retry_count'):
            number(getattr(self, name), name)
        for name in ('score', 'tokens', 'cost_usd', 'input_tokens', 'output_tokens'):
            number(getattr(self, name), name, True)
        for name in ('steps', 'tokens', 'tool_call_count', 'model_call_count', 'input_tokens', 'output_tokens', 'retry_count'):
            value = getattr(self, name)
            if value is not None and type(value) is not int:
                raise ValueError(f'{name} must be an integer')
        if not isinstance(self.metadata, dict):
            raise ValueError('Result metadata must be an object')
        canonical(asdict(self))


@dataclass
class BenchmarkRunSummary:
    task_count: int
    pass_count: int
    fail_count: int
    success_rate: float | None
    distributions: dict = field(default_factory=dict)
    failure_categories: dict = field(default_factory=dict)
    by_tag: dict = field(default_factory=dict)


@dataclass
class BenchmarkRun:
    id: str
    benchmark: str
    agent_config: AgentConfig
    started_at: str
    tasks: list[dict]
    suite_digest: str
    status: str = 'running'
    schema_version: str = 'benchmark-run.v1'
    summary: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
