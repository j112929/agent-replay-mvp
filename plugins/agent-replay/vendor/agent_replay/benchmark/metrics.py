"""Task-weighted metrics; unknown measurements stay unknown with coverage counts."""
from collections import Counter
from dataclasses import asdict
from statistics import mean, median
from ..rollout.metrics import percentile
from .contracts import BenchmarkRunSummary


def distribution(values):
    known = [v for v in values if v is not None]
    return {'count': len(known), 'missing': len(values) - len(known),
        'mean': mean(known) if known else None, 'median': median(known) if known else None,
        'p90': percentile(known, .90), 'p95': percentile(known, .95),
        'total': sum(known) if known else None}


def aggregate(results, grouped=True):
    passed = sum(r.success for r in results)
    summary = BenchmarkRunSummary(len(results), passed, len(results) - passed,
        passed / len(results) if results else None,
        distributions={key: distribution([getattr(r, key) for r in results]) for key in
            ('duration_ms', 'steps', 'tokens', 'cost_usd', 'tool_call_count', 'model_call_count',
             'input_tokens', 'output_tokens', 'retry_count')},
        failure_categories=dict(Counter(r.failure_category for r in results if not r.success)))
    if grouped:
        summary.by_tag = {tag: aggregate([r for r in results if tag in r.tags], False)
                          for tag in sorted({t for r in results for t in r.tags})}
    return asdict(summary)
