"""Trusted local tasks. Path-scoped file tools are not an OS security sandbox."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from ...storage import read, within
from ..contracts import BenchmarkTask, BenchmarkResult


from ..errors import ToolError, PolicyViolation


def command_output(command, cwd, timeout):
    # A process group lets deadlines stop shell children as well as the shell.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, cwd=cwd, stdout=stdout, stderr=stderr,
                                   start_new_session=True)
        try:
            process.wait(timeout=timeout)
        finally:
            # Also stop background descendants after an otherwise successful command.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        stdout.seek(0); stderr.seek(0)
        return {'exit_code': process.returncode,
                'stdout': stdout.read(65536).decode('utf-8', errors='replace'),
                'stderr': stderr.read(65536).decode('utf-8', errors='replace')}


class LocalEnvironment:
    def __init__(self, root, capture):
        self.root, self.capture = Path(root), capture

    def path(self, path):
        # /workspace is a virtual prefix for file tools. Shell uses cwd instead.
        path = str(path)
        if path == '/workspace':
            path = '.'
        elif path.startswith('/workspace/'):
            path = path[len('/workspace/'):]
        try:
            return within(self.root, path)
        except ValueError as exc:
            raise PolicyViolation(str(exc)) from exc

    def action(self, name, inputs, fn):
        with self.capture.span(name, kind='tool', boundary_id='benchmark.' + name,
                               input=inputs) as span:
            try:
                return span.set_output(fn())
            except ToolError as exc:
                if exc.metadata:
                    span.set_output(exc.metadata)
                raise
            except OSError as exc:
                if isinstance(exc, TimeoutError):
                    raise
                raise ToolError(str(exc), {'tool': name, 'errno': exc.errno}) from exc

    def execute(self, command):
        def execute():
            result = command_output(['/bin/sh', '-c', command], self.root, None)
            if result['exit_code']:
                raise ToolError(json.dumps(result), {'tool': 'shell', **result})
            return result
        return self.action('shell', {'command': command}, execute)

    def read_file(self, path):
        def read_text():
            p = self.path(path)
            if p.stat().st_size > 65536:
                raise ToolError('File exceeds 64 KiB capture limit')
            return p.read_text()
        return self.action('read_file', {'path': str(path)}, read_text)

    def write_file(self, path, content):
        def write_text():
            p = self.path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            return {'bytes': len(content.encode())}
        return self.action('write_file', {'path': str(path), 'content': content}, write_text)

    def list_files(self, path='.'):
        return self.action('list_files', {'path': str(path)},
                           lambda: sorted(p.name for p in self.path(path).iterdir()))


class LocalAdapter:
    def __init__(self, suite, verifier_timeout=10):
        self.suite = Path(suite).resolve()
        self.name = self.suite.name
        self.verifier_timeout = verifier_timeout
        self._workspaces = []

    def load_tasks(self):
        definitions = read(self.suite / 'tasks.json')
        if not isinstance(definitions, list) or not definitions:
            raise ValueError('tasks.json must contain a nonempty task array')
        tasks = []
        for definition in definitions:
            task = BenchmarkTask(**definition)
            verifier = within(self.suite, task.id + '/verifier.py')
            if not verifier.is_file():
                raise ValueError(f'Missing verifier for {task.id}')
            task.metadata = {**task.metadata,
                'verifier_sha256': hashlib.sha256(verifier.read_bytes()).hexdigest()}
            tasks.append(task)
        if len({t.id for t in tasks}) != len(tasks):
            raise ValueError('Duplicate task IDs')
        return tasks

    def prepare_environment(self, task, capture):
        directory = tempfile.TemporaryDirectory(prefix='agent-benchmark-')
        self._workspaces.append(directory)
        return LocalEnvironment(directory.name, capture)

    def final_state(self, task, environment):
        return {'workspace': str(environment.root)}

    def evaluate(self, task, trajectory, final_state):
        verifier = within(self.suite, task.id + '/verifier.py')
        if hashlib.sha256(verifier.read_bytes()).hexdigest() != task.metadata['verifier_sha256']:
            raise ValueError('Verifier changed during run')
        output = command_output([sys.executable, str(verifier), final_state['workspace']],
                                self.suite, self.verifier_timeout)
        if output['exit_code']:
            raise RuntimeError('Verifier crashed: ' + json.dumps(output))
        verdict = json.loads(output['stdout'])
        if not isinstance(verdict, dict) or type(verdict.get('success')) is not bool:
            raise ValueError('Verifier must emit a JSON object with boolean success')
        return BenchmarkResult(task.id, verdict['success'], score=verdict.get('score'),
            failure_category=verdict.get('failure_category') or (None if verdict['success'] else 'wrong_answer'),
            metadata={'verifier': verdict.get('metadata', {})}, tags=task.tags)

    def cleanup(self):
        while self._workspaces:
            self._workspaces.pop().cleanup()
