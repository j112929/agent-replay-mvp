"""Single-use release evidence from an operator-trusted CI signer.

HMAC authenticates that signer, not arbitrary code or a compromised CI host. The
controller never executes the candidate. Keep signing keys outside project data.
"""
from datetime import datetime
import hashlib
import hmac
import math
import os
import re
import secrets
import subprocess
import time

from ..contracts import validate_v2
from ..regression.cases import case_digest
from ..serialization import canonical, digest
from ..storage import read, save, within
from .imports import normalize
from .store import require_approval


CONFIG_FIELDS = {'model', 'prompt_version', 'tools_version', 'environment_version'}


def portable(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:/@-]{1,160}', value):
        raise ValueError('Invalid ' + label)
    return value


def configuration_digest(configuration):
    if not isinstance(configuration, dict) or set(configuration) != CONFIG_FIELDS:
        raise ValueError('Explicit model, prompt, tools and environment versions required')
    for value in configuration.values(): portable(value, 'configuration version')
    return digest(configuration)


def signing_key(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
        raise ValueError('Configure a 32-byte lowercase hex evidence signing key')
    return bytes.fromhex(value)


def trusted_runner(store):
    """Operator-only per-project configuration, never accepted in an HTTP body."""
    path = os.environ.get('GUARD_EVIDENCE_KEYS_FILE')
    if not path: raise ValueError('Trusted release runner is not configured')
    records = read(path, limit=65536)
    if not isinstance(records, dict): raise ValueError('Invalid trusted runner configuration')
    record = records.get(store.org_id + '/' + store.project_id)
    if not isinstance(record, dict) or set(record) != {'runner_id', 'secret'}:
        raise ValueError('Trusted release runner is not configured for this project')
    portable(record['runner_id'], 'runner identity'); signing_key(record['secret'])
    return record


def checkpoint_commit(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{40}', value):
        raise ValueError('A full Git commit SHA is required')
    return value


def money(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('Complete finite nonnegative USD cost is required')
    return value


def challenge_path(store, ident):
    if not isinstance(ident, str) or not re.fullmatch(r'[a-f0-9]{32}', ident):
        raise ValueError('Invalid release challenge ID')
    path = within(store.root, 'guard/challenges/' + ident + '.json')
    if path.is_symlink(): raise ValueError('Artifact aliases are forbidden')
    return path


def issue(store, case_id, baseline_id, commit, candidate_version, configuration,
          runner_id, max_cost_usd, ttl_seconds=900, version=None):
    """Operator chooses the release target/policy before the candidate executes."""
    checkpoint_commit(commit); portable(candidate_version, 'candidate version')
    portable(runner_id, 'runner identity'); money(max_cost_usd)
    if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= 3600:
        raise ValueError('Challenge lifetime must be 30–3600 seconds')
    config_digest = configuration_digest(configuration)
    with store.locked():
        path = store.case_path(case_id, version); case = read(path)
        require_approval(case, path, store.root)
        baseline = read(store.trace_path(baseline_id))
        from .runner import candidate_compatible
        candidate_compatible(case, baseline)
        if len(list((store.root / 'guard/challenges').glob('*.json'))) >= 1000:
            raise ValueError('Release challenge limit reached; archive the project')
        store.ensure_storage(8192)
        now = time.time()
        value = {'schema_version': 'release-challenge.v1', 'id': secrets.token_hex(16),
            'nonce': secrets.token_hex(32), 'org_id': store.org_id, 'project_id': store.project_id,
            'case_id': case['id'], 'case_version': case['guard']['version'],
            'case_digest': case_digest(case), 'fixture_digest': case['guard']['fixture_digest'],
            'baseline_id': baseline_id, 'baseline_digest': digest(baseline),
            'commit': commit, 'candidate_version': candidate_version,
            'configuration_digest': config_digest, 'runner_id': runner_id,
            'max_cost_usd': max_cost_usd, 'issued_at': now, 'expires_at': now + ttl_seconds}
        save(challenge_path(store, value['id']), value)
        return value


def capture_context(challenge, run_id, configuration):
    """Add to a *fresh* Capture metadata dict in customer-owned trusted CI code."""
    portable(run_id, 'CI run identity')
    if configuration_digest(configuration) != challenge['configuration_digest']:
        raise ValueError('Candidate configuration differs from the release target')
    return {'agent_version': challenge['candidate_version'],
        'release': {'challenge_id': challenge['id'], 'run_id': run_id,
                    'configuration_digest': challenge['configuration_digest']}}


def capture_started(trace):
    try:
        value = datetime.fromisoformat(trace['started_at'].replace('Z', '+00:00'))
        if value.tzinfo is None: raise ValueError()
        return value.timestamp()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError('Candidate start time is unavailable') from None


def trace_digest(trace):
    # The sanitizer adds location-specific display findings when the same trace is
    # nested in a bundle/report. Bind execution data, not those derived annotations.
    return digest({k: v for k, v in trace.items() if k != 'privacy'})


def bound_trace(challenge, trace, run_id):
    validate_v2(trace)
    from .generator import complete
    if not all(complete(item.get(field)) for item in [trace] + trace['steps'] for field in ('input', 'output')):
        raise ValueError('Complete input/output boundaries are required for a release')
    if trace.get('replay'): raise ValueError('Playback is not fresh candidate execution')
    privacy = trace.get('privacy')
    if not isinstance(privacy, dict) or privacy.get('status') != 'sanitized':
        raise ValueError('Sanitized candidate evidence is required')
    health = trace.get('capture_health', {})
    if not isinstance(health, dict) or health.get('state') != 'complete' or health.get('dropped_events', 0) or health.get('persistence_errors', 0):
        raise ValueError('Complete candidate capture health is required')
    metadata = trace.get('metadata', {})
    if not isinstance(metadata, dict): raise ValueError('Candidate metadata is unavailable')
    expected = {'challenge_id': challenge['id'], 'run_id': run_id,
                'configuration_digest': challenge['configuration_digest']}
    if metadata.get('release') != expected or metadata.get('agent_version') != challenge['candidate_version']:
        raise ValueError('Capture is not bound to the release run and configuration')
    provenance = trace.get('provenance')
    if not isinstance(provenance, dict) or not isinstance(provenance.get('code'), dict):
        raise ValueError('Candidate provenance is unavailable')
    code = provenance['code']
    if code.get('commit') != challenge['commit'] or code.get('dirty') is not False:
        raise ValueError('Candidate must capture the expected clean Git commit')
    started = capture_started(trace)
    if started < challenge['issued_at'] or started > time.time() + 5:
        raise ValueError('Candidate was captured outside the release window')
    duration = trace.get('execution', {}).get('duration_ms')
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
        raise ValueError('Candidate execution duration is unavailable')
    if started + duration / 1000 > time.time() + 5:
        raise ValueError('Candidate execution has not completed')
    if trace['execution']['status'] not in ('completed', 'error'):
        raise ValueError('Candidate execution is incomplete')


def attest(challenge, trace, key, repository, run_id, configuration, cost_usd):
    """Sign after a trusted runner executes its own code; no command execution."""
    raw_key = signing_key(key); portable(run_id, 'CI run identity'); money(cost_usd)
    capture_context(challenge, run_id, configuration)
    if not challenge['issued_at'] <= time.time() <= challenge['expires_at']:
        raise ValueError('Release challenge expired or has not started')
    def git(*args):
        result = subprocess.run(['git', '-C', str(repository), *args], capture_output=True, text=True, timeout=5)
        if result.returncode: raise ValueError('Trusted CI checkout is unavailable')
        return result.stdout.strip()
    if git('rev-parse', 'HEAD') != challenge['commit'] or git('status', '--porcelain'):
        raise ValueError('Trusted CI checkout must match the clean release commit')
    bound_trace(challenge, trace, run_id)
    trace = normalize(trace, 'native', challenge['org_id'], challenge['project_id'])
    manifest = {'schema_version': 'release-evidence.v1', 'challenge_id': challenge['id'],
        'challenge_digest': digest(challenge), 'trace_id': trace['id'], 'trace_digest': trace_digest(trace),
        'commit': challenge['commit'], 'runner_id': challenge['runner_id'], 'run_id': run_id,
        'configuration_digest': configuration_digest(configuration), 'cost_usd': cost_usd,
        'signed_at': time.time()}
    signature = hmac.new(raw_key, canonical(manifest).encode(), hashlib.sha256).hexdigest()
    return {'manifest': manifest, 'signature': signature, 'trace': trace}


def verify(store, case, baseline_id, candidate_id, bundle, key):
    """Called under the store lock. Consume authentic evidence once, even on FAIL.

    Invalid signatures do not consume the challenge. A crash after consumption
    fails closed: issue a new challenge and capture again, never retry old proof.
    """
    raw_key = signing_key(key)
    if not isinstance(bundle, dict) or set(bundle) != {'manifest', 'signature', 'trace'}:
        raise ValueError('Signed release evidence is required')
    manifest = bundle['manifest']; signature = bundle['signature']
    fields = {'schema_version', 'challenge_id', 'challenge_digest', 'trace_id', 'trace_digest',
              'commit', 'runner_id', 'run_id', 'configuration_digest', 'cost_usd', 'signed_at'}
    if not isinstance(manifest, dict) or set(manifest) != fields or manifest['schema_version'] != 'release-evidence.v1':
        raise ValueError('Invalid release evidence manifest')
    expected = hmac.new(raw_key, canonical(manifest).encode(), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(expected, signature):
        raise ValueError('Release evidence signature is invalid')
    path = challenge_path(store, manifest['challenge_id']); challenge = read(path)
    if challenge.get('consumed_at') is not None: raise ValueError('Release evidence has already been consumed')
    if digest(challenge) != manifest['challenge_digest']:
        raise ValueError('Release challenge changed')
    now = time.time(); signed = manifest['signed_at']
    if type(signed) not in (int, float) or not math.isfinite(signed) or not challenge['issued_at'] <= signed <= now + 5 or now > challenge['expires_at']:
        raise ValueError('Release evidence expired or has an invalid signing time')
    if challenge['org_id'] != store.org_id or challenge['project_id'] != store.project_id:
        raise ValueError('Release evidence belongs to another project')
    if challenge['case_id'] != case['id'] or challenge['case_version'] != case['guard']['version'] or challenge['case_digest'] != case_digest(case) or challenge['fixture_digest'] != case['guard']['fixture_digest']:
        raise ValueError('Release evidence targets a different approved case version')
    for field in ('commit', 'runner_id', 'configuration_digest'):
        if manifest[field] != challenge[field]: raise ValueError('Release target mismatch')
    portable(manifest['run_id'], 'CI run identity'); money(manifest['cost_usd'])
    if baseline_id != challenge['baseline_id'] or digest(read(store.trace_path(baseline_id))) != challenge['baseline_digest']:
        raise ValueError('Release baseline changed')
    trace = read(store.trace_path(candidate_id))
    if not isinstance(bundle['trace'], dict) or trace['id'] != manifest['trace_id'] or trace_digest(trace) != manifest['trace_digest'] or trace_digest(bundle['trace']) != manifest['trace_digest']:
        raise ValueError('Release candidate evidence changed')
    bound_trace(challenge, trace, manifest['run_id'])
    require_approval(case, store.case_path(case['id'], case['guard']['version']), store.root)
    store.ensure_storage(4096)
    save(path, {**challenge, 'consumed_at': now, 'manifest_digest': digest(manifest)})
    return {'verified': True, 'challenge_id': challenge['id'], 'manifest_digest': digest(manifest),
            'commit': manifest['commit'], 'runner_id': manifest['runner_id'], 'run_id': manifest['run_id'],
            'configuration_digest': manifest['configuration_digest'], 'cost_usd': manifest['cost_usd'],
            'max_cost_usd': challenge['max_cost_usd'],
            'trust': 'operator-trusted CI HMAC signer; not arbitrary-code execution attestation'}
