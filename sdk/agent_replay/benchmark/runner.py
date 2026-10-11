"""Sequential task execution using the existing Capture/journal/v2 pipeline."""
import asyncio
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import platform
import signal
import threading
import time
import uuid
from ..capture import Capture
from ..contracts import validate_v2
from ..migrations import to_v2
from ..serialization import digest
from ..storage import read, save
from .contracts import BenchmarkResult, BenchmarkRun, INFRA_FAILURES
from .store import RunStore
from .errors import ToolError, PolicyViolation, ToolLoop


class TaskTimeout(TimeoutError):
    pass


@contextmanager
def deadline(seconds):
    """POSIX main-thread deadline also interrupts blocking agent code."""
    if seconds is None:
        yield
        return
    def expired(*_):
        raise TaskTimeout('Task execution deadline exceeded')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def run_benchmark(adapter, agent, config, output):
    if not hasattr(signal, 'SIGALRM') or threading.current_thread() is not threading.main_thread():
        raise ValueError('Local benchmark runner requires a POSIX main thread for hard deadlines')
    if signal.getitimer(signal.ITIMER_REAL)[0]:
        raise ValueError('An existing process alarm conflicts with benchmark deadlines')
    tasks = adapter.load_tasks()
    if not tasks or len({t.id for t in tasks}) != len(tasks):
        raise ValueError('Benchmark needs nonempty, unique task IDs')
    definitions = [asdict(t) for t in tasks]
    run = BenchmarkRun(str(uuid.uuid4()), adapter.name, config,
        datetime.now(timezone.utc).isoformat(), definitions, digest(definitions),
        metadata={'python': platform.python_version(), 'platform': platform.platform(),
                  'adapter': type(adapter).__module__ + '.' + type(adapter).__qualname__,
                  'agent': type(agent).__module__ + '.' + type(agent).__qualname__})
    store = RunStore(output)
    store.create(run)
    results = []
    try:
        for task in tasks:
            started = time.perf_counter()
            capture = Capture('benchmark:' + task.id, store.directory / 'trajectories',
                input={'instruction': task.instruction}, durability='sync', on_capture_error='raise',
                metadata={'benchmark': {'name': adapter.name, 'task_id': task.id, 'run_id': run.id,
                    'agent_config': asdict(config), 'tags': task.tags, 'suite_digest': run.suite_digest}})
            result = None
            phase = 'environment'
            try:
                with capture:
                    with deadline(task.timeout_seconds):
                        environment = adapter.prepare_environment(task, capture)
                        phase = 'agent'
                        with capture.span('benchmark.agent', input={'instruction': task.instruction}) as span:
                            value = asyncio.run(agent.run(task, environment, capture))
                            capture.set_output(span.set_output(value))
                        phase = 'environment'
                        final_state = adapter.final_state(task, environment)
                    phase = 'verifier'
                    result = adapter.evaluate(task, to_v2(capture.trace), final_state)
                    if not isinstance(result, BenchmarkResult) or result.task_id != task.id:
                        raise ValueError('Adapter returned an invalid result or wrong task ID')
                    if not result.success:
                        capture.fail('Benchmark verifier rejected final state', result.failure_category)
            except Exception as exc:
                category = ('verifier_error' if phase == 'verifier' else
                    'environment_error' if phase == 'environment' else
                    'timeout' if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) else
                    'policy_violation' if isinstance(exc, PolicyViolation) else
                    'tool_error' if isinstance(exc, ToolError) else
                    'tool_loop' if isinstance(exc, ToolLoop) else 'agent_error')
                result = BenchmarkResult(task.id, False, failure_category=category,
                    metadata={'error': {'type': type(exc).__name__, 'message': str(exc)}, 'phase': phase,
                              'detail': getattr(exc, 'metadata', {})})
            finally:
                try:
                    adapter.cleanup()
                except Exception as exc:
                    prior = asdict(result) if result else None
                    result = BenchmarkResult(task.id, False, failure_category='environment_error',
                        metadata={'phase': 'cleanup', 'error': str(exc), 'previous_result': prior})
            if capture.path is None:
                raise OSError('Trajectory persistence failed; run remains incomplete')
            trajectory = validate_v2(read(capture.path))
            trajectory['evaluation'] = {'verdict': 'pass' if result.success else
                'error' if result.failure_category in INFRA_FAILURES else 'fail',
                'benchmark': {'success': result.success, 'score': result.score,
                              'failure_category': result.failure_category}}
            if not result.success and not trajectory.get('failures'):
                trajectory['failures'] = [{'id': str(uuid.uuid4()), 'category': result.failure_category,
                                          'message': 'Benchmark task failed', 'step_id': None}]
            save(capture.path, trajectory)
            result.duration_ms = round((time.perf_counter() - started) * 1000, 3)
            result.tags = task.tags
            result.steps = len(trajectory['steps'])
            result.tool_call_count = sum(s['kind'] == 'tool' for s in trajectory['steps'])
            result.tool_failure_count = sum(s['kind']=='tool' and s['execution']['status']=='error' for s in trajectory['steps'])
            models = [s for s in trajectory['steps'] if s['kind'] == 'llm']
            result.model_call_count = len(models)
            result.retry_count = sum(max(0, s.get('attempt', 1) - 1) for s in trajectory['steps'])
            for target, field in (('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens'), ('tokens', 'total_tokens')):
                counts = [s.get('usage', {}).get(field) for s in models]
                setattr(result, target, sum(counts) if all(type(v) is int and v >= 0 for v in counts) else None)
            result.cost_usd = 0 if not models else result.cost_usd
            result.trajectory_id = trajectory['id']
            result.trajectory_path = str(capture.path.relative_to(store.directory))
            result.metadata['task_digest'] = digest(asdict(task))
            result.__post_init__()
            results.append(result)
            store.persist(run, results)
        from .metrics import aggregate
        run.summary = aggregate(results)
        run.status = 'completed'
        run.metadata['finished_at'] = datetime.now(timezone.utc).isoformat()
        store.persist(run, results)
        return run, results
    except BaseException:
        run.status = 'interrupted'
        store.persist(run, results)
        raise
