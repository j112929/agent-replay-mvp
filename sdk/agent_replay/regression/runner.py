import uuid
from pathlib import Path
from .cases import load,case_digest
from .assertions import evaluate
from .reports import write_reports
from ..experiments import execute,resolve_spec
from ..storage import save,read

def run_case(path,project,directory,allow_live=False,cancel=None):
    rid=str(uuid.uuid4());result={'schema_version':'1.0','id':rid,'case_id':Path(path).stem,'verdict':'error','assertions':[]}
    try:
        case,trace=load(path,project['_root'])
        if not case.get('assertions') or not any(a.get('op') not in ('execution_completed','no_unhandled_error') for a in case['assertions']):raise ValueError('Case needs at least one business assertion')
        spec=resolve_spec({**case['replay_spec'],'entrypoint_id':case['entrypoint_id']},project,Path(path).parent)
        if any(r.get('mode')=='live' for r in spec.get('model_rules',[])) and not allow_live:raise ValueError('Live model rules require --live; default CI is offline')
        result.update(case_id=case['id'],case_digest=case_digest(case),source_digest=case['source']['sha256'],fixture_digest=spec.get('fixture_set_digest'))
        baseline=case.get('baseline')
        result['baseline_state']='stale' if baseline and (baseline.get('case_digest')!=case_digest(case) or baseline.get('fixture_digest')!=spec.get('fixture_set_digest')) else 'accepted' if baseline else 'none'
        if baseline:
            import hashlib
            baseline_path=(Path(path).parent/baseline.get('result_path','')).resolve()
            if not baseline_path.is_relative_to(Path(project['_root']).resolve()) or not baseline_path.is_file() or hashlib.sha256(baseline_path.read_bytes()).hexdigest()!=baseline.get('sha256'):
                result['baseline_state']='stale'
        if case.get('candidate'):
            import hashlib
            from ..storage import within
            from ..contracts import validate_v2
            from ..comparison import compare
            from ..security.sanitizer import sanitize_artifact
            reference = case['candidate']
            candidate_path = (Path(path).parent / reference['trace_path']).resolve()
            within(project['_root'], candidate_path)
            if hashlib.sha256(candidate_path.read_bytes()).hexdigest() != reference['sha256']:
                raise ValueError('Candidate evidence checksum mismatch')
            captured = validate_v2(sanitize_artifact(read(candidate_path)))
            if captured.get('replay') or captured.get('metadata', {}).get('candidate_entrypoint') != case['entrypoint_id']:
                result.update(verdict='inconclusive', reason='Candidate identity mismatch or playback evidence')
                return result
            captured['fidelity'] = {'level': 'captured_candidate', 'gaps': ['Evaluates captured evidence, does not execute candidate code']}
            save(Path(directory)/'runs'/'traces'/(captured['id']+'.json'), captured)
            experiment = {'id': str(uuid.uuid4()), 'duration_ms': captured['execution']['duration_ms'],
                'state': 'completed', 'trace': captured, 'comparison': compare(trace,captured)}
        else:
            experiment=execute(trace,spec,project,Path(directory)/'runs',cancel=cancel)
        result.update(experiment_id=experiment['id'],duration_ms=experiment['duration_ms'])
        if experiment['state']!='completed':
            result.update(reason=experiment.get('error',{}).get('message','Execution unavailable'),verdict='inconclusive' if experiment.get('error',{}).get('type') == 'ReplayDenied' else 'error');return result
        candidate=experiment['trace'];error=candidate['execution'].get('error',{})
        result.update(execution_status=candidate['execution']['status'],candidate_commit=candidate['provenance']['code'].get('commit'),fidelity=candidate['fidelity'],candidate_trace_id=candidate['id'],comparison=experiment['comparison'])
        incomplete = next((s.get('error',{}) for s in candidate['steps'] if (s.get('error') or {}).get('type') in ('ReplayMismatch','RecordedError')), None)
        if incomplete: error=incomplete
        if error.get('type') in ('ReplayMismatch','RecordedError'):
            result.update(verdict='inconclusive',reason=error['message'])
        elif error.get('type') in ('ProviderError',):result.update(verdict='error',reason=error['message'])
        else:
            result['assertions']=evaluate(candidate,case['assertions']);result['verdict']='pass' if all(a['verdict']=='pass' for a in result['assertions']) and not candidate.get('failures') else 'fail'
    except Exception as exc:result.update(verdict='error',reason=str(exc))
    finally:save(Path(directory)/(rid+'.json'),result)
    return result

def run_suite(paths,project,directory,allow_live=False,cancel=None):
    if not paths:raise ValueError('Suite is empty')
    results=[]
    for path in paths:
        if cancel and cancel.is_set():break
        case=read(path)
        live=any(r.get('mode')=='live' for r in case.get('replay_spec',{}).get('model_rules',[]))
        policy=case.get('live_policy',{})
        repetitions=policy.get('repetitions',5) if live and allow_live else 1
        if type(repetitions) is not int or not 1<=repetitions<=100:raise ValueError('Live repetitions must be 1–100')
        runs=[]
        for _ in range(repetitions):
            if cancel and cancel.is_set():break
            runs.append(run_case(path,project,directory,allow_live,cancel))
        if not runs:break
        if repetitions>1:
            aggregate=dict(runs[-1]);aggregate['repetitions']=len(runs);aggregate['runs']=[{'id':r['id'],'verdict':r['verdict']} for r in runs]
            aggregate['pass_rate']=sum(r['verdict']=='pass' for r in runs)/repetitions
            threshold=policy.get('min_pass_rate')
            if type(threshold) not in (int,float) or not 0<=threshold<=1:raise ValueError('Live case requires min_pass_rate in [0,1]')
            aggregate['verdict']='error' if any(r['verdict']=='error' for r in runs) else 'inconclusive' if any(r['verdict']=='inconclusive' for r in runs) or len(runs)<repetitions else 'pass' if aggregate['pass_rate']>=threshold else 'fail'
            results.append(aggregate)
        else:results.extend(runs)
    write_reports(results,directory)
    code=130 if cancel and cancel.is_set() else 2 if any(r['verdict']=='error' for r in results) else 3 if any(r['verdict']=='inconclusive' for r in results) else 1 if any(r['verdict']=='fail' for r in results) else 0
    return {'results':results,'exit_code':code,'report_dir':str(directory)}
