"""Bounded JSON experiment configs; no extra parser/runtime dependencies."""
from dataclasses import dataclass, field, asdict
from ...planning.contracts import finite


@dataclass
class ExperimentConfig:
    tasks: int = 40
    seeds: list = field(default_factory=lambda:[0,1,2])
    candidate_count: int = 4
    samples: int = 16
    horizon: int = 3
    horizons: list = field(default_factory=lambda:[1,2,4,8,16,32])
    risk_weight: float = .5
    alpha: float = .9
    uncertainty_weight: float = .2
    min_groups: int = 20
    seed: int = 17
    screening: bool = False
    rare_failure_scale: float = 1.
    replan_interval: int = 2
    error_threshold: float = .75
    uncertainty_threshold: float = .5
    max_runs: int = 32
    max_total_predictions: int = 1000000
    max_experiment_seconds: int = 1800
    max_artifact_mb: int = 1024

    def __post_init__(self):
        for name,lo,hi in (('tasks',2,100),('candidate_count',2,8),('samples',2,32),('horizon',1,32),('min_groups',2,1000),('seed',0,2**31-1),('replan_interval',1,32),('max_runs',1,64),('max_total_predictions',1,2000000),('max_experiment_seconds',1,3600),('max_artifact_mb',1,4096)):
            if type(getattr(self,name)) is not int or not lo<=getattr(self,name)<=hi:raise ValueError('Invalid experiment config: '+name)
        if not isinstance(self.seeds,list) or not 1<=len(self.seeds)<=8 or any(type(s) is not int or not 0<=s<10000 for s in self.seeds) or len(set(self.seeds))!=len(self.seeds):raise ValueError('Invalid unique trial seeds')
        if not isinstance(self.horizons,list) or not self.horizons or len(self.horizons)>6 or any(type(h) is not int or not 1<=h<=32 for h in self.horizons) or len(set(self.horizons))!=len(self.horizons):raise ValueError('Invalid horizons')
        if type(self.screening) is not bool or type(self.rare_failure_scale) not in (float,int) or self.rare_failure_scale not in (1.,.1):raise ValueError('Invalid screening/model version')
        for name in ('risk_weight','uncertainty_weight','error_threshold'):finite(getattr(self,name),name)
        finite(self.alpha,'CVaR confidence',0,.999999)
        finite(self.uncertainty_threshold,'uncertainty threshold',0,1)


def from_dict(value):
    if not isinstance(value,dict):raise ValueError('Experiment config must be an object')
    try:return ExperimentConfig(**value)
    except TypeError:raise ValueError('Unknown experiment configuration field') from None
