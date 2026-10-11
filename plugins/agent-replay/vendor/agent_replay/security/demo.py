"""Synthetic model-version regression, sanitized token, blocked payment replay."""
from dataclasses import asdict
import json
from pathlib import Path
from ..benchmark.adapters.local import LocalAdapter
from ..benchmark.contracts import AgentConfig
from ..benchmark.runner import run_benchmark
from ..benchmark.release import release_gate, failure_analysis
from ..benchmark.compare import compare_runs
from ..benchmark.reports import write_reports
from ..storage import read,save
from .replay import playback, ToolInterceptor, ReplayDenied

CANARY='sk-synthetic-only-demo-0123456789012345'


class SyntheticVersionAgent:
    def __init__(self,version): self.version=version
    async def run(self,task,environment,capture):
        with capture.span('synthetic.model',kind='llm',input={'model':self.version},model=self.version) as step:
            step.event['usage']={'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}
            step.set_output({'api_key':CANARY,'answer':'hello' if self.version=='A' else 'wrong'})
        # An attempted mutating call intercepted as an explicit recorded simulation.
        record={'output':{'present':True,'replayability':'complete','value':{'blocked':True,'simulated':True}},
                'execution':{'status':'completed'}}
        with capture.span('payment.charge',kind='tool',input={'amount':100,'Authorization':CANARY},tool={'side_effect':'write'}) as step:
            step.set_output(ToolInterceptor().resolve(record))
        environment.write_file('result.txt','hello' if self.version=='A' else 'wrong')
        return {'model_version':self.version,'synthetic':True}


def demo(directory):
    root=Path(directory).resolve();root.mkdir(parents=True,exist_ok=False)
    suite=root/'suite';(suite/'coding-task').mkdir(parents=True)
    save(suite/'tasks.json',[{'id':'coding-task','instruction':'Write hello to result.txt',
                            'tags':['critical','coding'],'timeout_seconds':5}])
    (suite/'coding-task'/'verifier.py').write_text("import json,sys\nfrom pathlib import Path\nprint(json.dumps({'success':(Path(sys.argv[1])/'result.txt').read_text()=='hello'}))\n")
    for version,label in [('A','baseline'),('B','candidate')]:
        run_benchmark(LocalAdapter(suite),SyntheticVersionAgent(version),
            AgentConfig('synthetic-model',model=version,parameters={'synthetic':True}),root/label)
    comparison=compare_runs(root/'baseline',root/'candidate')
    gate=release_gate(root/'baseline',root/'candidate',{'environment':'PRODUCTION','min_tasks':20})
    if gate['decision']!='FAIL':raise AssertionError('Critical regression must block release')
    save(root/'gate.json',gate)
    result=read(root/'candidate'/'results.json')[0]
    trace=read(root/'candidate'/result['trajectory_path'])
    replay=playback(trace)
    save(root/'replayed.json',replay)
    blocked=False
    try:ToolInterceptor().resolve(policy='ALLOW_IN_SANDBOX')
    except ReplayDenied:blocked=True
    comparison['quality_gate']=gate
    comparison['failure_analysis']=failure_analysis(root/'baseline',root/'candidate')
    reports=write_reports(root/'candidate',comparison=comparison)
    for path in root.rglob('*'):
        if path.is_file() and CANARY.encode() in path.read_bytes():
            raise AssertionError('Synthetic canary leaked')
    summary={'synthetic':True,'models_are_simulated':True,'canary_leaked':False,
        'external_operations':0,'state_mutation_blocked':blocked,'gate':gate['decision'],
        'newly_failing':comparison['newly_failing'],'reports':reports}
    save(root/'demo.json',summary)
    return summary
