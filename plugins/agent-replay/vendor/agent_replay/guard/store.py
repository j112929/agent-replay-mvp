"""Single-controller versioned artifacts and lifetime project caps; no billing DB."""
from contextlib import contextmanager
import datetime as dt
import hashlib
from pathlib import Path
import re
import uuid

from ..storage import read, save, within
from ..serialization import canonical, digest
from ..regression import cases
from ..regression.assertions import validate_rules
from .imports import normalize, checked_json
from ..security.sanitizer import Sanitizer
from .generator import propose, identity

DEFAULT_LIMITS = {'imports': 100, 'cases': 50, 'runs': 500, 'storage_bytes': 64 * 1024 * 1024}


class QuotaExceeded(ValueError):
    pass


def now(): return dt.datetime.now(dt.timezone.utc).isoformat()


def reference(path, base):
    import os
    return {'trace_path': os.path.relpath(path, base), 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def resolve_reference(ref, base, root):
    if not isinstance(ref, dict) or set(ref) != {'trace_path', 'sha256'}:
        raise ValueError('Invalid evidence reference')
    path = within(root, (Path(base) / ref['trace_path']).resolve())
    if hashlib.sha256(path.read_bytes()).hexdigest() != ref['sha256']:
        raise ValueError('Evidence checksum changed; regenerate and review the case')
    return path


def require_approval(case, path, root):
    guard = case.get('guard')
    if not isinstance(guard, dict) or guard.get('validation', {}).get('status') != 'validated':
        raise ValueError('Generated test is not validated')
    path = within(root, Path(path).resolve())
    control = within(root, path.parent / ('control-' + str(guard['version']) + '.json'))
    if control.exists():
        state = read(control)
        if state.get('enabled') is not True or state.get('review_state') == 'rejected':
            raise ValueError('Required case version is disabled or rejected')
    approval = read(within(root, path.parent / ('approval-' + str(guard['version']) + '.json')))
    if approval.get('state') != 'approved' or approval.get('case_digest') != cases.case_digest(case):
        raise ValueError('Exact case version requires explicit approval')
    resolve_reference(case['source'], path.parent, root)
    positive = resolve_reference(guard['positive'], path.parent, root)
    fixture = {'schema_version': '1.0', 'fixtures': case['replay_spec']['fixtures']}
    if digest(fixture) != guard.get('fixture_digest'): raise ValueError('Fixture version changed')
    validate_rules(case['assertions'])
    # Approval cannot bless a fabricated client-side validation state.
    checked = propose(read(resolve_reference(case['source'], path.parent, root)), read(positive), {'assertions': case['assertions']})
    if checked['validation']['status'] != 'validated' or digest(checked['fixtures']) != guard['fixture_digest']:
        raise ValueError('Validation evidence changed')
    return approval


class GuardStore:
    def __init__(self, root, org_id='local', project_id='default', limits=None, sensitive_fields=()):
        self.root = Path(root).resolve()
        self.org_id, self.project_id = org_id, project_id
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}
        if set(self.limits) != set(DEFAULT_LIMITS) or any(type(v) is not int or v < 1 for v in self.limits.values()):
            raise ValueError('Invalid operator project limits')
        self.sensitive_fields = sensitive_fields

    @contextmanager
    def locked(self):
        try: import fcntl
        except ImportError: raise ValueError('Safe quota locking unavailable on this platform') from None
        path = within(self.root, 'guard/.lock'); path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(stream, fcntl.LOCK_UN)

    def usage(self):
        files = []
        for path in self.root.rglob('*'):
            if path.is_symlink(): raise ValueError('Artifact aliases are forbidden')
            if path.is_file() and path.name != '.lock': files.append(path)
            if len(files) > 10000: raise QuotaExceeded('Artifact count limit exceeded')
        return {'imports': len(list((self.root / 'traces').glob('*.json'))),
                'cases': len(list((self.root / 'guard/cases').glob('*/versions/*.case.json'))),
                'runs': len(list((self.root / 'guard/runs').glob('*/gate.json'))),
                'storage_bytes': sum(p.stat().st_size for p in files), 'model_calls': 0,
                'limits': self.limits, 'scope': 'lifetime project caps; not subscription entitlements'}

    def ensure(self, operation, reserve_bytes):
        usage = self.usage()
        if usage[operation] >= self.limits[operation]: raise QuotaExceeded('Project ' + operation + ' quota exceeded')
        if usage['storage_bytes'] + reserve_bytes > self.limits['storage_bytes']:
            raise QuotaExceeded('Project storage quota exceeded')

    def import_trace(self, payload, format='native'):
        trace = normalize(payload, format, self.org_id, self.project_id, self.sensitive_fields)
        target = within(self.root, 'traces/' + digest(trace) + '.json')
        with self.locked():
            for path in (self.root / 'traces').glob('*.json'):
                old = read(path)
                if old.get('id') == trace['id']:
                    if canonical(old) != canonical(trace): raise ValueError('Trace ID conflicts with existing evidence')
                    return {'id': trace['id'], 'duplicate': True, 'privacy': trace['privacy']}
            self.ensure('imports', len(canonical(trace).encode()) + 1)
            save(target, trace)
        return {'id': trace['id'], 'duplicate': False, 'privacy': trace['privacy']}

    def trace_path(self, ident):
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', ident): raise ValueError('Invalid trace ID')
        for path in (self.root / 'traces').glob('*.json'):
            within(self.root, path)
            if path.is_symlink(): raise ValueError('Artifact aliases are forbidden')
            if read(path).get('id') == ident: return path
        raise ValueError('Unknown trace')

    def case_path(self, ident, version=None):
        try: uuid.UUID(ident)
        except (ValueError, TypeError, AttributeError): raise ValueError('Invalid guard case ID') from None
        base = within(self.root, 'guard/cases/' + ident + '/versions')
        versions = [int(p.name.split('.')[0]) for p in base.glob('*.case.json') if p.name.split('.')[0].isdigit()]
        if not versions: raise ValueError('Unknown regression case')
        version = max(versions) if version is None else version
        if type(version) is not int or version < 1: raise ValueError('Invalid case version')
        path = within(self.root, base / (str(version) + '.case.json'))
        if not path.exists(): raise ValueError('Unknown case version')
        return path

    def list_cases(self):
        values = []
        for folder in (self.root / 'guard/cases').glob('*'):
            if folder.is_dir(): values.append(self.get_case(folder.name))
        return values

    def get_case(self, ident, version=None):
        path = self.case_path(ident, version); case = read(path)
        try: review = require_approval(case, path, self.root)
        except (ValueError, OSError): review = {'state': 'draft' if case['guard']['validation']['status'] == 'validated' else 'incomplete'}
        control_path = within(self.root, path.parent / ('control-' + str(case['guard']['version']) + '.json'))
        control = read(control_path) if control_path.exists() else {'enabled': True}
        if control.get('review_state') == 'rejected': review = {'state': 'rejected'}
        elif control.get('enabled') is not True: review = {'state': 'disabled'}
        return {'case': case, 'review': review, 'control': control}

    def control(self, ident, operation, reviewer, version=None):
        if operation not in ('enable', 'disable', 'reject'): raise ValueError('Invalid case control')
        if not isinstance(reviewer, str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{1,120}', reviewer): raise ValueError('Reviewer required')
        with self.locked():
            path = self.case_path(ident, version); case = read(path)
            if operation == 'reject' and (path.parent / ('approval-' + str(case['guard']['version']) + '.json')).exists():
                raise ValueError('Disable an approved version instead of rejecting it')
            target = within(self.root, path.parent / ('control-' + str(case['guard']['version']) + '.json'))
            if target.exists() and read(target).get('review_state') == 'rejected' and operation == 'enable':
                raise ValueError('Rejected versions require regeneration and review')
            state = {'enabled': operation == 'enable', 'review_state': 'rejected' if operation == 'reject' else 'reviewed',
                     'reviewer': reviewer, 'updated_at': now(), 'version': case['guard']['version']}
            self.ensure_storage(2048); save(target, state)
        return self.get_case(ident, version)

    def generate(self, source_id, positive_id=None, expected=None, case_id=None):
        source_path = self.trace_path(source_id)
        positive_path = self.trace_path(positive_id) if positive_id else None
        source = read(source_path); positive = read(positive_path) if positive_path else None
        if expected is not None:
            cleaner = Sanitizer(sensitive_fields=self.sensitive_fields)
            expected = cleaner.sanitize(checked_json(expected))
            if cleaner.failed: raise ValueError('Expected behavior could not be sanitized')
        proposal = propose(source, positive, expected)
        with self.locked():
            if case_id:
                previous = read(self.case_path(case_id)); version = previous['guard']['version'] + 1
            else: case_id = str(uuid.uuid4()); version = 1
            target = within(self.root, f'guard/cases/{case_id}/versions/{version}.case.json')
            reserve = 2 * source_path.stat().st_size + len(canonical(proposal).encode()) + 8192
            self.ensure('cases', reserve)
            agent_id = source.get('metadata', {}).get('agent_id', 'unresolved')
            if not isinstance(agent_id, str) or not agent_id: agent_id = 'unresolved'
            case = cases.create(source_path, agent_id, target)
            case.update(id=case_id, title=source['name'], assertions=proposal['assertions'], tags=['guard', 'critical'])
            case['replay_spec'].update(fixtures=proposal['fixtures']['fixtures'], limits={'max_model_calls': 0, 'max_steps': 200})
            case['guard'] = {'version': version, 'generated_at': now(), 'generator': 'deterministic-expectation.v1',
                'replay_mode': 'RECORDED_REPLAY', 'enabled': True, 'critical': True,
                'fixture_digest': digest(proposal['fixtures']), 'validation': proposal['validation'],
                'reason': proposal['reason'], 'expected_provenance': 'operator-supplied expected behavior',
                'positive': reference(positive_path, target.parent) if positive_path else None,
                'compatibility': {'agent_id': agent_id, 'input_digest': digest(source['input'])},
                'failure_description': source.get('failures') or [{'category': 'agent_error'}],
                'privacy_limitations': source.get('privacy', {}).get('limitations', [])}
            save(target, case)
        return self.get_case(case_id, version)

    def approve(self, ident, reviewer, version=None):
        if not isinstance(reviewer, str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{1,120}', reviewer):
            raise ValueError('Explicit reviewer identity is required')
        with self.locked():
            path = self.case_path(ident, version); case = read(path)
            control = path.parent / ('control-' + str(case['guard']['version']) + '.json')
            if control.exists() and read(control).get('enabled') is not True:
                raise ValueError('Disabled or rejected versions cannot be approved')
            positive = case['guard'].get('positive')
            if not positive or case['guard']['validation']['status'] != 'validated':
                raise ValueError('Incomplete verifier cannot be approved')
            proposal = propose(read(resolve_reference(case['source'], path.parent, self.root)),
                read(resolve_reference(positive, path.parent, self.root)), {'assertions': case['assertions']})
            if proposal['validation']['status'] != 'validated' or digest(proposal['fixtures']) != case['guard']['fixture_digest']:
                raise ValueError('Verifier evidence is not valid')
            target = path.parent / ('approval-' + str(case['guard']['version']) + '.json')
            if target.exists():
                return require_approval(case, path, self.root)
            self.ensure_storage(2048)
            review = {'state': 'approved', 'reviewer': reviewer, 'approved_at': now(),
                      'version': case['guard']['version'], 'case_digest': cases.case_digest(case)}
            save(target, review)
            require_approval(case, path, self.root)
        return review

    def ensure_storage(self, reserve):
        if self.usage()['storage_bytes'] + reserve > self.limits['storage_bytes']:
            raise QuotaExceeded('Project storage quota exceeded')
