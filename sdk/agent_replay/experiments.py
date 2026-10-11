"""Run candidate application code in a bounded worker, persist every outcome."""
import contextlib
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from .storage import read,save,within
from .serialization import digest
from .comparison import compare
from .runtime.code_runner import checkout

class Cancelled(RuntimeError): pass

def resolve_spec(spec,project,base=None):
    spec=dict(spec)
    allowed={'schema_version','id','source_trace_id','source_digest','scope','policy','entrypoint_id','limits','fixtures','fixture_set','fixture_set_digest','model_rules','tool_rules','code_ref','determinism','environment_profile'}
    if set(spec)-allowed:raise ValueError('Unsupported replay spec fields: '+', '.join(sorted(set(spec)-allowed)))
    if spec.get('environment_profile') and spec['environment_profile'] not in project.get('environment_profiles',{}):raise ValueError('Unknown environment profile')
    if spec.get('scope','agent')!='agent':raise ValueError('Use replay --step for single-step experiments')
    if spec.get('policy','frozen') not in ('frozen','hybrid'):raise ValueError('Invalid replay policy')
    if not spec.get('entrypoint_id'):raise ValueError('Entrypoint ID required')
    limits={'timeout_seconds':60,'max_steps':2000,'max_model_calls':0,**spec.get('limits',{})}
    for k,v in limits.items():
        if not isinstance(v,(int,float)) or isinstance(v,bool) or v<0:raise ValueError('Invalid limit '+k)
    if not 0<limits['timeout_seconds']<=3600:raise ValueError('Timeout must be between 0 and 3600 seconds')
    if 'max_cost_usd' in limits:raise ValueError('Cost estimation is unavailable; use max_model_calls and output token limits')
    if set(limits)-{'timeout_seconds','max_steps','max_model_calls','max_tokens','max_cost_usd'}:raise ValueError('Unsupported budget')
    spec['limits']=limits
    for k in ('max_steps','max_model_calls','max_tokens'):
        if k in limits and type(limits[k]) is not int:raise ValueError('Budget must be integer: '+k)
    for k in spec.get('determinism',{}):
        if k not in ('clock_fixture','rng_seed','state_adapter_id'):raise ValueError('Unsupported determinism control: '+k)
    if spec.get('fixture_set'):
        path=(Path(base or project['_root'])/spec.pop('fixture_set')).resolve()
        if not path.is_relative_to(Path(project['_root']).resolve()):raise ValueError('Fixture path escapes project')
        value=read(path);spec['fixtures']=value['fixtures'];spec['fixture_set_digest']=digest(value)
    for r in spec.get('model_rules',[]):
        if r.get('mode') not in ('frozen','live'):raise ValueError('Invalid model mode')
        if r.get('mode')=='live' and (r.get('provider_id') not in project.get('providers',{}) or not r.get('model')):raise ValueError('Unregistered model provider or missing model')
    for r in spec.get('tool_rules',[]):
        if r.get('mode') not in ('frozen','local'):raise ValueError('Use fixture_set for fixture substitution')
        if r.get('mode')=='local' and r.get('implementation_id') not in project.get('tools',{}):raise ValueError('Unregistered tool implementation')
    return spec

def execute(trace,spec,project,directory='.replay',cancel=None):
    """Executable legacy requests fail closed; use safe-playback for recorded data."""
    from .security.replay import isolation_status
    result = {'schema_version': '1.0', 'id': str(uuid.uuid4()),
        'source_trace_id': trace.get('id'), 'state': 'failed', 'duration_ms': 0,
        'error': {'type': 'ReplayDenied', 'message': isolation_status()['reason']},
        'security': {'execution_kind': 'SANDBOX_EXECUTION', 'sandbox': isolation_status()}}
    save(Path(directory)/'experiments'/(result['id']+'.json'), result)
    return result
