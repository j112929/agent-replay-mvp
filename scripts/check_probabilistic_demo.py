"""Check actual persisted CI evidence, including the existing CLI quality gate."""
import json
from pathlib import Path
import subprocess
import sys
from agent_replay.storage import read,save
from agent_replay.benchmark.store import RunStore
from agent_replay.security.replay import playback

root=Path(sys.argv[1])
for name in ('calibration','risk','horizon'):
    record=read(root/name/'experiment.json')
    assert record['status']=='completed' and record['synthetic'] is True
    assert record['inference']['external_model_calls']==0
    for run in record['runs'].values():
        metadata,results=RunStore(root/name/run['path']).load()
        assert all(r.metadata['probabilistic']['outcome']['status']=='verified' for r in results)
        trace=read(root/name/run['path']/results[0].trajectory_path)
        assert playback(trace)['replay']['external_operations']==0
baseline=root/'risk/expected_value';candidate=root/'risk/cvar'
policy={'environment':'DEVELOPMENT','min_tasks':2,'critical_tags':[],
    'probabilistic':{'baseline_run_id':read(baseline/'run.json')['id'],
        'min_labeled_samples':200,'min_groups':20,'max_brier_regression':.02,'max_severe_failure_regression':.02}}
save(root/'policy.json',policy)
result=subprocess.run([sys.executable,'-m','agent_replay.cli','benchmark','gate','--baseline',str(baseline),
    '--candidate',str(candidate),'--policy',str(root/'policy.json'),'--output',str(root/'quality-gate.json')],capture_output=True,text=True)
assert result.returncode==0,result.stderr
assert read(root/'quality-gate.json')['decision']=='INCONCLUSIVE'
# A self-comparison is a positive control, with explicitly sufficient test coverage.
policy['probabilistic'].update(min_labeled_samples=2,min_groups=2)
save(root/'positive-policy.json',policy)
result=subprocess.run([sys.executable,'-m','agent_replay.cli','benchmark','gate','--baseline',str(baseline),
    '--candidate',str(baseline),'--policy',str(root/'positive-policy.json'),'--output',str(root/'positive-gate.json')],capture_output=True,text=True)
assert result.returncode==0,result.stderr
assert read(root/'positive-gate.json')['decision']=='PASS'
print('Verified actual trajectories, recorded replay, and PASS/INCONCLUSIVE CLI gates; FAIL is covered by unit tests.')
