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
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    spec=resolve_spec(spec,project)
    eid=str(uuid.uuid4());start=time.monotonic()
    result={'schema_version':'1.0','id':eid,'source_trace_id':trace['id'],'source_digest':digest(trace),'spec':spec,'spec_id':digest(spec),'state':'running'}
    save(directory/'experiments'/f'{eid}.json',result)
    context=checkout(project,spec['code_ref']) if spec.get('code_ref') else contextlib.nullcontext(project)
    try:
        with context as selected, tempfile.TemporaryDirectory(prefix='replay-worker-') as temp:
            inp=Path(temp)/'request.json';out=Path(temp)/'result.json'
            save(inp,{'trace':trace,'spec':spec,'project':selected,'directory':str(directory/'traces')})
            env=os.environ.copy();env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
            profile=selected.get('environment_profiles',{}).get(spec.get('environment_profile'),{})
            interpreter=profile.get('interpreter',sys.executable)
            with (Path(temp)/'worker.log').open('w') as log:
                process=subprocess.Popen([interpreter,'-m','agent_replay.runtime.worker',str(inp),str(out)],env=env,cwd=selected['_root'],stdout=log,stderr=log,start_new_session=True)
                try:
                    deadline=time.monotonic()+spec['limits']['timeout_seconds']
                    while process.poll() is None:
                        if cancel and cancel.is_set():raise Cancelled('Cancelled by user')
                        if time.monotonic()>deadline:raise TimeoutError('Worker time budget exceeded')
                        time.sleep(.05)
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid,signal.SIGTERM)
                        try:process.wait(timeout=2)
                        except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
            result['worker_exit_code']=process.returncode
            if not out.is_file():raise RuntimeError('Worker exited without a result; inspect capture journal')
            payload=read(out)
            if payload.get('error'):result.update(state='failed',error=payload['error'])
            else:
                candidate=payload['trace'];report=compare(trace,candidate)
                result.update(state='completed',trace=candidate,candidate_trace_id=candidate['id'],comparison=report)
                save(directory/'comparisons'/f"{report['id']}.json",report)
    except (Exception,KeyboardInterrupt) as exc:
        result.update(state='cancelled' if isinstance(exc,(Cancelled,KeyboardInterrupt)) else 'interrupted' if isinstance(exc,TimeoutError) else 'failed',error={'type':type(exc).__name__,'message':str(exc)})
    result['duration_ms']=round((time.monotonic()-start)*1000,3)
    save(directory/'experiments'/f'{eid}.json',result)
    return result
