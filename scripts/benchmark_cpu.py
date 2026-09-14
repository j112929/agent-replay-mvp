"""Independent cold-start CPU repetitions; never substitutes CPU numbers for GPUs."""
import argparse
import json
from pathlib import Path
import statistics
from agent_replay.rollout.async_runner import run
from agent_replay.storage import save

p=argparse.ArgumentParser();p.add_argument('--directory',required=True);p.add_argument('--repetitions',type=int,default=3);args=p.parse_args()
if not 1<=args.repetitions<=10:raise SystemExit('repetitions must be 1..10')
root=Path(args.directory).resolve();root.mkdir(parents=True,exist_ok=True)
reports=[]
for executor in ('process','ray'):
    for index in range(args.repetitions):
        config={'executor':executor,'actors':3,'verifiers':1,'updates':4,'batch_size':16,'max_inflight':32,'max_policy_lag':0,'timeout_seconds':180,'seed':100}
        report=run(config,root/f'{executor}-{index}')
        reports.append(report)
        print(json.dumps({'executor':executor,'repetition':index+1,'rollouts_per_second':report['rollout_throughput_per_second']}),flush=True)
summary={}
for executor in ('process','ray'):
    matching=[r for r in reports if r['config']['executor']==executor]
    values=[r['rollout_throughput_per_second'] for r in matching]
    summary[executor]={'repetitions':len(values),'throughput_median':statistics.median(values),'throughput_min':min(values),'throughput_max':max(values),'latency_p95_median':statistics.median(r['end_to_end_training_step_latency_seconds']['p95'] for r in matching),'actor_idle_fraction_median':statistics.median(r['actor_idle']['fraction'] for r in matching),'learner_idle_fraction_median':statistics.median(r['learner_idle']['fraction'] for r in matching)}
save(root/'results.json',{'reports':reports,'summary':summary,'scope':'CPU bandit orchestration; cold startup included; GPU and token throughput unmeasured'})
print(json.dumps(summary,indent=2))
