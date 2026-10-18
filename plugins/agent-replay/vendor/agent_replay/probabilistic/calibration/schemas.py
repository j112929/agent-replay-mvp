"""Forecasts are timestamped evidence, not retrospective model confidence labels."""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import math
from ...planning.contracts import data


def now():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    try:
        result = datetime.fromisoformat(value[:-1]+'+00:00' if value.endswith('Z') else value) if isinstance(value, str) else value
        if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise ValueError('Timezone-aware timestamp required') from None


def probability(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('Probability must be finite within [0, 1]')
    return value


VERSIONS = ('policy_version', 'model_version', 'benchmark_version', 'environment_version', 'tool_version')


@dataclass
class SuccessForecast:
    forecast_id: str
    run_id: str
    task_id: str
    trajectory_id: str
    task_group: str
    policy_version: str
    model_version: str
    benchmark_version: str
    environment_version: str
    tool_version: str
    probability: float
    forecast_timestamp: str
    information_cutoff: str
    source: str = 'raw_agent'
    scope: str = 'task_success'
    prediction_horizon: int | None = None
    epistemic_uncertainty: float | None = None
    aleatoric_uncertainty: float | None = None
    confidence_status: str = 'uncalibrated'
    provenance: dict = field(default_factory=dict)
    schema_version: str = 'success-forecast.v1'

    def __post_init__(self):
        for name in ('forecast_id', 'run_id', 'task_id', 'trajectory_id', 'task_group', 'source') + VERSIONS:
            if not isinstance(getattr(self, name), str) or not 1 <= len(getattr(self, name)) <= 200:
                raise ValueError('Missing forecast identity/version: ' + name)
        probability(self.probability)
        self.forecast_timestamp=timestamp(self.forecast_timestamp).isoformat()
        self.information_cutoff=timestamp(self.information_cutoff).isoformat()
        if timestamp(self.information_cutoff) > timestamp(self.forecast_timestamp):
            raise ValueError('Information cutoff cannot follow forecast')
        if self.scope not in ('task_success', 'action_outcome', 'world_model_confidence'):
            raise ValueError('Unsupported forecast scope')
        if self.prediction_horizon is not None and (type(self.prediction_horizon) is not int or not 1 <= self.prediction_horizon <= 32):
            raise ValueError('Invalid forecast horizon')
        for value in (self.epistemic_uncertainty, self.aleatoric_uncertainty):
            if value is not None:
                probability(value)
        if self.confidence_status not in ('uncalibrated', 'empirical_uncalibrated', 'fitted_unvalidated'):
            raise ValueError('Calibration cannot self-certify')
        if self.schema_version != 'success-forecast.v1':
            raise ValueError('Unsupported forecast schema')
        self.provenance = data(self.provenance)

    def to_dict(self):
        return asdict(self)


@dataclass
class VerifiedOutcome:
    run_id: str
    task_id: str
    trajectory_id: str
    task_group: str
    outcome_timestamp: str
    success: bool | None
    benchmark_version: str
    environment_version: str
    tool_version: str
    status: str = 'verified'
    source: str = 'real_execution_verifier'
    schema_version: str = 'verified-outcome.v1'

    def __post_init__(self):
        self.outcome_timestamp=timestamp(self.outcome_timestamp).isoformat()
        if self.source != 'real_execution_verifier' or self.schema_version != 'verified-outcome.v1':
            raise ValueError('Verified real execution outcome required')
        if self.status not in ('verified', 'missing', 'censored', 'infrastructure_error'):
            raise ValueError('Invalid label status')
        if self.status == 'verified' and type(self.success) is not bool or self.status != 'verified' and self.success is not None:
            raise ValueError('Missing/censored outcome cannot carry a label')
        for name in ('run_id', 'task_id', 'trajectory_id', 'task_group', 'benchmark_version', 'environment_version', 'tool_version'):
            if not isinstance(getattr(self, name), str) or not 1 <= len(getattr(self, name)) <= 200:
                raise ValueError('Missing outcome identity/version')


@dataclass
class CalibrationResult:
    brier_score: float | None
    log_loss: float | None
    ece: float | None
    sample_count: int
    reliability_bins: list
    confidence_intervals: dict
