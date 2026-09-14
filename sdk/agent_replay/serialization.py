"""Canonical portable JSON and loss-aware value envelopes."""
import hashlib
import json
import math

MISSING = object()

def canonical(value):
    def check(v):
        if isinstance(v, float):
            if not math.isfinite(v):
                raise ValueError('Non-finite numbers are not portable JSON')
            if v.is_integer():
                v = int(v)
        if isinstance(v, int) and not isinstance(v, bool) and abs(v) > 9007199254740991:
            raise ValueError('Large integers must be encoded as strings')
        if isinstance(v, dict):
            if not all(isinstance(k, str) for k in v):
                raise ValueError('JSON keys must be strings')
            return {k: check(x) for k, x in v.items()}
        if isinstance(v, list):
            return [check(x) for x in v]
        return v
    return json.dumps(check(value), sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def envelope(value=MISSING):
    if value is MISSING:
        return {'present': False, 'replayability': 'unsupported'}
    encoded = canonical(value)
    state = 'redacted' if '[REDACTED]' in encoded else 'truncated' if '[MAX_DEPTH]' in encoded else 'unsupported' if '[CAPTURE_ERROR]' in encoded or isinstance(value,str) and value.startswith('<') and value.endswith('>') else 'complete'
    return {'present': True, 'value': value, 'replayability': state}

def unwrap(value, default=None):
    return value.get('value', default) if value.get('present') else default
