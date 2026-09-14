import asyncio
import inspect
from pathlib import Path
from ..capture import Capture
from ..migrations import legacy,to_v2
from ..project import callable_for
from ..serialization import digest
from ..storage import save
from .policy import Policy
from .fidelity import doctor

async def async_run(trace,spec,project,directory):
    preflight=doctor(trace,project,spec['entrypoint_id'])
    if not preflight['ready']: raise ValueError('; '.join(preflight['gaps']))
    source=legacy(trace); policy=Policy(source,spec,project)
    run=Capture(source['name'],directory,input=source['input'],entrypoint_id=spec['entrypoint_id'],durability='sync',on_capture_error='raise',_policy=policy)
    policy.run=run
    d=spec.get('determinism',{})
    if 'rng_seed' in d: run.random.seed(d['rng_seed'])
    if 'clock_fixture' in d: run.clock=lambda:d['clock_fixture']
    run.trace['replay']={'source_trace_id':trace['id'],'source_digest':digest(trace),'mode':'agent_rerun','scope':'agent','application_validated':False,'spec_id':digest(spec)}
    try:
        with run:
            if d.get('state_adapter_id'):
                run.state = callable_for(project,'state_adapters',d['state_adapter_id'])(run.input)
            value=callable_for(project,'entrypoints',spec['entrypoint_id'])(run)
            if inspect.isawaitable(value): value=await value
            run.set_output(value)
            policy.fixtures.verify()
    except Exception:
        if run.path is None: raise
    result=to_v2(run.trace)
    result['fidelity']={'level':'live_variant' if policy.calls else 'controlled_boundaries','gaps':preflight['limitations'], 'matched_steps':len(policy.matcher.used),'unused_recordings':[s['id'] for s in source['steps'] if s['id'] not in policy.matcher.used], 'model_calls':policy.calls}
    save(run.path,result)
    return result

def run(trace,spec,project,directory): return asyncio.run(async_run(trace,spec,project,directory))
