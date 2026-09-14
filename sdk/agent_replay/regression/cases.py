import hashlib
from pathlib import Path
from ..storage import read,save
from ..serialization import digest
from ..schema import validate_trace

def create(source,entrypoint,output):
    output=Path(output).resolve();trace=validate_trace(read(source))
    target=output.parent/'sources'/(digest(trace)+'.json');save(target,trace)
    case={'schema_version':'1.0','id':output.name.removesuffix('.case.json'),'title':trace['name'],'tags':['offline'],'source':{'trace_path':str(target.relative_to(output.parent)),'sha256':hashlib.sha256(target.read_bytes()).hexdigest()},'entrypoint_id':entrypoint,'replay_spec':{'scope':'agent','policy':'frozen','limits':{'max_model_calls':0}},'assertions':[]}
    save(output,case);return case

def case_digest(case):return digest({k:v for k,v in case.items() if k!='baseline'})

def load(path,root):
    path=Path(path).resolve();case=read(path)
    if case.get('schema_version')!='1.0' or not case.get('id') or not case.get('entrypoint_id'):raise ValueError('Invalid case')
    source=(path.parent/case['source']['trace_path']).resolve()
    if not source.is_relative_to(Path(root).resolve()):raise ValueError('Source outside project root')
    if hashlib.sha256(source.read_bytes()).hexdigest()!=case['source']['sha256']:raise ValueError('Case source checksum mismatch')
    return case,validate_trace(read(source))

def accept(case_path,result_path):
    case=read(case_path);result=read(result_path)
    source=(Path(case_path).parent/case['source']['trace_path']).resolve()
    if hashlib.sha256(source.read_bytes()).hexdigest()!=case['source']['sha256']:raise ValueError('Source changed since this case was created')
    fixture=case.get('replay_spec',{}).get('fixture_set')
    if fixture and digest(read(Path(case_path).parent/fixture))!=result.get('fixture_digest'):raise ValueError('Fixture changed since the candidate ran')
    if not result.get('assertions') or not all(a.get('verdict')=='pass' for a in result['assertions']):raise ValueError('Passing assertion evidence is required')
    if result.get('verdict')!='pass' or result.get('case_digest')!=case_digest(case) or result.get('case_id')!=case['id'] or result.get('execution_status')!='completed' or result.get('fidelity',{}).get('level') not in ('controlled_boundaries','live_variant'):
        raise ValueError('Accept requires a complete passing candidate run for this exact case')
    path=Path(case_path).parent/'baselines'/(result['id']+'.json');save(path,result)
    case['baseline']={'result_path':str(path.relative_to(Path(case_path).parent)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'case_digest':case_digest(case),'fixture_digest':result.get('fixture_digest')}
    save(case_path,case);return case
