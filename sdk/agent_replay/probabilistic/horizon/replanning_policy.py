"""Observation-only trigger policies. Mandatory executor checks always apply."""
from dataclasses import dataclass
from ...planning.contracts import finite


@dataclass
class ReplanningPolicy:
    kind: str = 'periodic'
    interval: int = 1
    error_threshold: float = .75
    uncertainty_threshold: float = .5

    def __post_init__(self):
        if self.kind not in ('open_loop','periodic','error_triggered','uncertainty_triggered'):
            raise ValueError('Unknown replanning strategy')
        if type(self.interval) is not int or not 1<=self.interval<=32: raise ValueError('Invalid replan interval')
        finite(self.error_threshold,'replan error threshold')
        finite(self.uncertainty_threshold,'replan uncertainty threshold',0,1)

    def trigger(self, steps_since_plan, prediction_error, uncertainty, plan_available=True):
        if not plan_available:return 'initial_or_exhausted_plan'
        if self.kind=='periodic' and steps_since_plan>=self.interval:return 'periodic_interval'
        if self.kind=='error_triggered' and prediction_error is not None and prediction_error>self.error_threshold:return 'observed_prediction_error'
        if self.kind=='uncertainty_triggered' and uncertainty>self.uncertainty_threshold:return 'predictive_uncertainty'
        return None
