"""Recompute grouped research statistics from immutable, verified benchmark runs.

No environment/model execution. References original trajectories rather than copies.
"""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
from statistics import mean
import subprocess
import time
import uuid
import hashlib
from agent_replay.storage import read,save,within
from agent_replay.serialization import digest
from agent_replay.benchmark.store import RunStore
from agent_replay.probabilistic.gates import _load
from agent_replay.probabilistic.experiments.config import from_dict
from agent_replay.probabilistic.experiments.runner import compare_metric
from agent_replay.probabilistic.experiments.statistics import bootstrap,holm
from agent_replay.probabilistic.planning.risk_metrics import cvar
from agent_replay.probabilistic.experiments.reporting import write_report
from agent_replay.provenance import collect

parser=argparse.ArgumentParser();parser.add_argument('experiment');parser.add_argument('--output',required=True)
args=parser.parse_args();source=Path(args.experiment).resolve();dest=Path(args.output).resolve()
e=read(source/'experiment.json',limit=32*1024*1024)
if e.get('status')!='completed' or e.get('synthetic') is not True:raise ValueError('Completed synthetic source required')
config=from_dict(e['config']);dest.mkdir(parents=True,exist_ok=False);started=time.perf_counter();rows={};runs={}
for name,r in e['runs'].items():
    path=within(source,r['path']);run,evidence,checked=_load(path)
    if r['run_id']!=run['id']:raise ValueError('Run reference mismatch')
    records=evidence['rows'];index={r['task_id']:r for r in checked}
    if len(records)!=len(index) or {r['task_id'] for r in records}!=set(index):raise ValueError('Metric row coverage mismatch')
    for record in records:
        trusted=index[record['task_id']]
        if record['task_group']!=trusted['task_group'] or record['trial_seed']!=trusted['trial_seed']:raise ValueError('Metric pairing mismatch')
        record.update({k:trusted[k] for k in ('success','severe_failure','brier_score')})
    rows[name]=records
    summary={**r['summary'],'labeled_count':sum(r['label_status']=='verified' for r in records),
             'label_coverage':sum(r['label_status']=='verified' for r in records)/len(records)}
    summary['empirical_loss_cvar']=cvar([-r['cumulative_reward'] for r in records],config.evaluation_alpha)
    summary['evaluation_loss_alpha']=config.evaluation_alpha
    summary['confidence_intervals']={field:bootstrap(records,lambda rr:mean(r[field] for r in rr),config.seed)
        for field in ('success','severe_failure','cumulative_reward','planning_latency_ms','cost_usd') if all(r.get(field) is not None for r in records)}
    summary['confidence_intervals']['empirical_loss_cvar']=bootstrap(records,lambda rr:cvar([-r['cumulative_reward'] for r in rr],config.evaluation_alpha),config.seed)
    _,results=RunStore(path).load();trace=read(within(path,results[0].trajectory_path));sessions=trace.get('metadata',{}).get('planning',{}).get('sessions',[])
    example=None
    if sessions and sessions[0].get('research',{}).get('sidecar_path'):
        record=read(within(path,sessions[0]['research']['sidecar_path']),limit=16*1024*1024)
        if digest(record)!=sessions[0]['research']['sidecar_digest']:raise ValueError('Decision sidecar hash mismatch')
        example={'selected_candidate_id':record['selected_candidate_id'],'candidate_evaluations':record['candidate_evaluations']}
    runs[name]={**r,'summary':summary,'candidate_example':example,'path':os.path.relpath(path,dest)}
comparisons=[]
if e['experiment']=='risk_planning':
    pairs=[('expected_value',name) for name in ('greedy','mean_variance','cvar','uncertainty_cvar')]
    pairs += [('cvar',name) for name in runs if name.startswith('screen-')]
    for base,candidate in pairs:
        for field in ('success','severe_failure','cumulative_reward','cost_usd','duration_ms'):
            comparisons.append({'baseline':base,'candidate':candidate,'metric':field,
                **compare_metric(rows[base],rows[candidate],field,config.seed,config.min_groups)})
else:comparisons=e.get('comparisons',[])
for record,p in zip(comparisons,holm([r['p_value'] for r in comparisons])):
    record['holm_p_value']=p
    if p>.05:record['decision']='INCONCLUSIVE'
def dominates(a,b):
    fields=(('success_rate',-1),('severe_failure_rate',1),('planning_latency_ms',1),('cost_usd',1))
    if any(a.get(f) is None or b.get(f) is None for f,_ in fields):return False
    return all(sign*a[f]<=sign*b[f] for f,sign in fields) and any(sign*a[f]<sign*b[f] for f,sign in fields)
pareto=[name for name,r in runs.items() if not any(other!=name and dominates(s['summary'],r['summary']) for other,s in runs.items())]
import agent_replay
package_root=Path(agent_replay.__file__).parent
analysis_source=digest({p.relative_to(package_root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(package_root.rglob('*.py'))})
result={**e,'experiment_id':str(uuid.uuid4()),'runs':runs,'comparisons':comparisons,'descriptive_pareto_front':pareto,
    'analysis':{'source_experiment_id':e['experiment_id'],'source_digest':digest(e),
        'git_commit_sha':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'environment_provenance':collect(),'python_source_digest':analysis_source,
        'elapsed_seconds':time.perf_counter()-started,'model_or_tool_calls':0,
        'method':'Rejoin verified labels, cluster-bootstrap paired effects, Holm correction over the full declared screen; no refit or model rerun'}}
save(dest/'experiment.json',result);write_report(dest,result)
print(json.dumps({'output':str(dest),'runs':len(runs),'comparisons':len(comparisons),'pareto_front':pareto}))
