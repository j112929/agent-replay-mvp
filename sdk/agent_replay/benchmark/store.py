"""Atomic file-backed run store, with explicit incomplete-run rejection."""
from dataclasses import asdict
from pathlib import Path
from ..storage import read, save, within
from .contracts import BenchmarkResult


class RunStore:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()

    def create(self, run):
        self.directory.mkdir(parents=True, exist_ok=False)
        save(self.directory / 'run.json', asdict(run))
        save(self.directory / 'results.json', [])

    def persist(self, run, results):
        save(self.directory / 'results.json', [asdict(r) for r in results])
        save(self.directory / 'run.json', asdict(run))

    def load(self, complete=True):
        run = read(self.directory / 'run.json')
        if run.get('schema_version') != 'benchmark-run.v1':
            raise ValueError('Unsupported benchmark run schema')
        results = [BenchmarkResult(**r) for r in read(self.directory / 'results.json')]
        ids = [r.task_id for r in results]
        task_ids = [t['id'] for t in run['tasks']]
        if len(set(ids)) != len(ids) or len(set(task_ids)) != len(task_ids):
            raise ValueError('Duplicate task IDs in run')
        if complete and (run['status'] != 'completed' or set(ids) != set(task_ids) or not ids):
            raise ValueError('Benchmark run is incomplete or empty')
        for result in results:
            if result.trajectory_path:
                within(self.directory, result.trajectory_path)
        return run, results


def list_runs(directory):
    root = Path(directory)
    return [read(p) for p in sorted(root.glob('*/run.json'))]
