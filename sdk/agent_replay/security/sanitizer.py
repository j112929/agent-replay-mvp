"""Recursive field-aware sanitization. No raw values in findings or exceptions."""
import dataclasses
import datetime
import json
import math
from pathlib import Path
import re

REDACTED = '[REDACTED]'
FAILED = '[SANITIZATION_FAILED]'
CRITICAL = frozenset(('authorization', 'proxyauthorization', 'apikey', 'password',
    'passwd', 'secret', 'clientsecret', 'accesstoken', 'refreshtoken', 'token',
    'idtoken', 'privatekey', 'cookie', 'setcookie', 'credentials', 'sessiontoken'))
PII = frozenset(('email', 'ssn', 'socialsecuritynumber', 'phone', 'phonenumber',
                 'dateofbirth', 'address', 'fullname'))
PATTERNS = (
    ('private_key', re.compile(r'-----BEGIN (?:[A-Z0-9 ]*PRIVATE KEY)-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|$)')),
    ('authorization', re.compile(r'(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+')),
    ('provider_key', re.compile(r'\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b')),
    ('credential_assignment', re.compile(r'''(?ix)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|client[_-]?secret|authorization|token)\s*[:=]\s*["']?[^\s,"';}\]]+''')),
    ('url_credentials', re.compile(r'(?i)(?:https?|postgres(?:ql)?|mysql|mongodb)://[^/\s:@]+:[^/\s@]+@')),
)


def critical(key):
    return key in CRITICAL or key.endswith(('apikey','password','privatekey','accesstoken','refreshtoken','secretaccesskey','accesskeyid'))


def normalized(key):
    return re.sub(r'[^a-z0-9]', '', key.lower())


class Sanitizer:
    def __init__(self, policy='STANDARD', sensitive_fields=(), patterns=(), pii=False):
        if policy not in ('STRICT', 'STANDARD', 'CUSTOM'):
            raise ValueError('Unknown sanitization policy')
        if not isinstance(sensitive_fields, (list,tuple,set)) or not all(isinstance(x, str) and x for x in sensitive_fields):
            raise ValueError('Sensitive field names must be strings')
        self.policy = policy
        self.fields = CRITICAL | {normalized(x) for x in sensitive_fields}
        self.pii = pii or policy == 'STRICT'
        if self.pii:
            self.fields |= PII
        if not isinstance(patterns, (list,tuple)):
            raise ValueError('Patterns must be a sequence of trusted rules')
        try:
            self.patterns = PATTERNS + tuple(('custom', re.compile(x)) for x in patterns)
        except (re.error, TypeError):
            raise ValueError('Invalid sanitizer pattern') from None
        self.findings = []
        self.failed = False

    def finding(self, path, method):
        # paths only contain sanitized keys; never derive placeholders from secret hashes.
        item = {'path': path, 'method': method}
        if item not in self.findings:
            self.findings.append(item)
        return REDACTED

    def text(self, value, path):
        for method, pattern in self.patterns:
            value = pattern.sub(lambda m: self.finding(path, method), value)
        if self.pii:
            value = re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', lambda m: self.finding(path, 'pii_email'), value)
            value = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', lambda m: self.finding(path, 'pii_ssn'), value)
        return value

    def sanitize(self, value, path='', _seen=None, _depth=0):
        seen = set() if _seen is None else _seen
        try:
            if _depth > 30:
                self.failed = True
                self.finding(path, 'depth_limit')
                return FAILED
            if isinstance(value, str):
                # Serialized tool arguments and HTTP JSON bodies are structured data too.
                if value.lstrip().startswith(('{', '[')):
                    try:
                        parsed = json.loads(value)
                    except (ValueError, RecursionError):
                        self.failed = True
                        self.finding(path, 'malformed_structured_text')
                        return FAILED
                    if isinstance(parsed, (dict, list)):
                        return json.dumps(self.sanitize(parsed, path, seen, _depth + 1), ensure_ascii=False)
                return self.text(value, path)
            if value is None or type(value) in (bool, int):
                return value
            if isinstance(value, float):
                return value if math.isfinite(value) else str(value)
            if isinstance(value, (datetime.datetime, datetime.date, Path)):
                return self.text(str(value), path)
            if id(value) in seen:
                self.failed = True
                self.finding(path, 'cycle')
                return FAILED
            original_id = id(value)
            seen.add(original_id)
            try:
                if hasattr(value, 'model_dump'):
                    value = value.model_dump(mode='json')
                elif dataclasses.is_dataclass(value) and not isinstance(value, type):
                    value = {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
                if isinstance(value, dict):
                    result = {}
                    for k, v in value.items():
                        key = self.text(str(k), path + '/<key>')
                        pointer = path + '/' + key.replace('~', '~0').replace('/', '~1')
                        sensitive = normalized(key) in self.fields or critical(normalized(key)) or (key.lower() == 'value' and isinstance(value.get('name'), str) and critical(normalized(value['name'])))
                        result[key] = self.finding(pointer, 'structured_field') if sensitive else self.sanitize(v, pointer, seen, _depth + 1)
                    # Revise value envelopes after redaction, rather than claiming complete replayability.
                    if result.get('present') is True and 'replayability' in result and 'value' in result:
                        encoded = json.dumps(result['value'])
                        if FAILED in encoded:
                            result['replayability'] = 'unsupported'
                        elif REDACTED in encoded:
                            result['replayability'] = 'redacted'
                    return result
                if isinstance(value, (list, tuple)):
                    return [self.sanitize(v, path + '/' + str(i), seen, _depth + 1) for i, v in enumerate(value)]
                self.failed = True
                self.finding(path, 'unsupported_type')
                return FAILED
            finally:
                seen.discard(original_id)
        except Exception:
            self.failed = True
            self.finding(path, 'sanitizer_failure')
            return FAILED

    def metadata(self):
        return {'policy': self.policy, 'status': 'failed_closed' if self.failed else 'sanitized',
            'redacted_field_count': len(self.findings), 'redacted_fields': list(self.findings),
            'classification': 'restricted' if self.findings or self.failed else 'unclassified',
            'replay_compatibility': 'fixtures_required' if self.findings or self.failed else 'recorded',
            'limitations': ['Redacted data cannot be reconstructed; executable reruns require isolated fixtures'] if self.findings or self.failed else [],
            'required_mocked_responses': [x['path'] for x in self.findings]}


def sanitize(value):
    return Sanitizer().sanitize(value)


def sanitize_artifact(value):
    """Sanitize persisted/exposed JSON and attach metadata to trajectory objects."""
    sanitizer = Sanitizer()
    result = sanitizer.sanitize(value)
    def annotate(node):
        if isinstance(node, dict):
            if node.get('schema_version') in ('1.0', '2.0') and isinstance(node.get('steps'), list):
                old = node.get('privacy', {})
                metadata = sanitizer.metadata()
                if isinstance(old, dict) and old.get('status') in ('sanitized', 'failed_closed'):
                    findings = old.get('redacted_fields', []) + metadata['redacted_fields']
                    metadata['redacted_fields'] = list({json.dumps(f, sort_keys=True): f for f in findings}.values())
                    metadata['redacted_field_count'] = len(metadata['redacted_fields'])
                    if old.get('status') == 'failed_closed': metadata['status'] = 'failed_closed'
                    if old.get('replay_compatibility') == 'fixtures_required':
                        metadata.update(replay_compatibility='fixtures_required', limitations=old.get('limitations', []))
                    metadata['policy'] = old.get('policy', metadata['policy'])
                if metadata['redacted_field_count'] or metadata['status'] == 'failed_closed':
                    metadata['classification'] = 'restricted'
                    metadata['replay_compatibility'] = 'fixtures_required'
                    metadata['required_mocked_responses'] = [x['path'] for x in metadata['redacted_fields']]
                    metadata['limitations'] = ['Redacted data cannot be reconstructed; isolated fixtures required for executable reruns']
                node['privacy'] = metadata
            for k, child in list(node.items()):
                if k != 'privacy': annotate(child)
        elif isinstance(node, list):
            for child in node: annotate(child)
    annotate(result)
    return result
