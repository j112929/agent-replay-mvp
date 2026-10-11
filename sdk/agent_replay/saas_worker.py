"""Fixed JSON bridge for the single-controller SaaS. Never executes customer code."""
import json
from pathlib import Path
import sys
import uuid

from .guard.store import GuardStore
from .guard.runner import run
from .guard.imports import MAX_PAYLOAD
from .storage import read, within
from .security.sanitizer import sanitize
from .security.replay import playback


def execute(store, action, body):
    if action == 'import': return store.import_trace(body['trace'], body.get('format', 'native'))
    if action == 'generate': return store.generate(body['source'], body.get('positive'), body.get('expected'), body.get('case_id'))
    if action == 'approve': return store.approve(body['case_id'], body['reviewer'], body.get('version'))
    if action == 'control': return store.control(body['case_id'], body['operation'], body['reviewer'], body.get('version'))
    if action == 'evaluate': return run(store, body['case_id'], body.get('baseline'), body.get('candidate'), body.get('version'))
    if action == 'case': return store.get_case(body['case_id'], body.get('version'))
    if action == 'cases': return {'items': store.list_cases()}
    if action == 'trace': return read(store.trace_path(body['id']))
    if action == 'playback': return playback(read(store.trace_path(body['id'])))
    if action == 'usage': return store.usage()
    if action == 'traces':
        values = []
        for path in sorted((store.root / 'traces').glob('*.json'), key=lambda x: x.stat().st_mtime, reverse=True):
            trace = read(within(store.root, path))
            values.append({k: trace.get(k) for k in ('id', 'name', 'started_at', 'execution', 'failures', 'privacy', 'metadata')})
        return {'items': values}
    if action == 'runs':
        paths = sorted((store.root / 'guard/runs').glob('*/gate.json'), key=lambda x: x.stat().st_mtime, reverse=True)
        return {'items': [read(within(store.root, p)) for p in paths[:100]]}
    if action in ('run', 'report'):
        try: uuid.UUID(body['id'])
        except (ValueError, KeyError, TypeError): raise ValueError('Invalid run ID') from None
        base = within(store.root, 'guard/runs/' + body['id'])
        if action == 'run': return read(within(store.root, base / 'gate.json'))
        return {'html': within(store.root, base / 'reports/index.html').read_text()}
    raise ValueError('Unsupported worker operation')


def main():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
        resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
        # Root and tenant IDs come only from the controller's fixed process arguments.
        root, org, project = sys.argv[1:4]
        data = sys.stdin.buffer.read(MAX_PAYLOAD + 65537)
        if len(data) > MAX_PAYLOAD + 65536: raise ValueError('Worker input too large')
        request = json.loads(data)
        if not isinstance(request, dict) or not isinstance(request.get('body'), dict): raise ValueError('Invalid worker request')
        store = GuardStore(root, org, project, limits=request.get('limits'), sensitive_fields=request.get('sensitive_fields', ()))
        result = execute(store, request.get('action'), request['body'])
        print(json.dumps({'ok': True, 'value': result}, ensure_ascii=False, allow_nan=False))
    except (ValueError, OSError, KeyError, TypeError, RecursionError):
        # No payload/exception interpolation: sanitizer failures cannot become log leaks.
        print(json.dumps({'ok': False, 'error': 'Operation rejected: check schema, fixtures, approval and quota limits'}))
        return 2
    return 0


if __name__ == '__main__': sys.exit(main())
