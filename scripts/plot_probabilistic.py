"""Standalone research figures from persisted public numbers; matplotlib optional.

python scripts/plot_probabilistic.py dist/reliability-summary.json --output docs/research/figures
"""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

parser=argparse.ArgumentParser();parser.add_argument('artifact');parser.add_argument('--output',required=True)
args=parser.parse_args();value=json.loads(Path(args.artifact).read_text())
assert value['schema_version']=='reliability-public.v1' and value['synthetic'] is True
out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
plt.rcParams.update({'font.size':10,'svg.hashsalt':'agent-replay-v06'})
def finish(fig,name,note=None):
    fig.text(.02,.01,'Synthetic CPU workflow · repeated scenario seeds · descriptive screening; no production inference',fontsize=8)
    if note:fig.text(.02,.04,note,fontsize=8)
    fig.tight_layout(rect=(0,.08 if note else .04,1,1));fig.savefig(out/(name+'.svg'),metadata={'Date':None});plt.close(fig)
cal=value['experiments'].get('calibration',{}).get('calibration',{})
if cal:
    fig,axes=plt.subplots(1,2,figsize=(11,4.5))
    for split,ax in zip(('test','shift'),axes):
        methods=cal.get(split,{})
        for name,m in methods.items():
            bins=m['reliability_bins'];ax.plot([b['predicted'] for b in bins],[b['observed'] for b in bins],marker='o',label=f'{name} (BS={m["brier_score"]:.4f}, n={m["sample_count"]})')
        ax.plot([0,1],[0,1],color='gray',linestyle='--');ax.set(xlim=(0,1),ylim=(0,1),xlabel='Predicted success',ylabel='Verified success frequency',title=f'{split}: reliability')
        ax.grid(alpha=.2);ax.legend(fontsize=8,loc='upper left')
    finish(fig,'v06-calibration','The held-out test has one observed class; discrimination is unavailable. See report for group counts and intervals.')
risk=value['experiments'].get('risk_planning',{}).get('runs',{})
if risk:
    methods=[k for k in ('greedy','expected_value','mean_variance','cvar','uncertainty_cvar') if k in risk]
    fig,axes=plt.subplots(1,2,figsize=(11,4.5))
    for ax,field,interval_field in ((axes[0],'severe_failure_rate','severe_failure'),(axes[1],'planning_latency_ms','planning_latency_ms')):
        values=[risk[k][field] for k in methods]
        intervals=[risk[k].get('confidence_intervals',{}).get(interval_field) for k in methods]
        if all(ci is not None for ci in intervals):
            errors=[[max(0,v-ci[0]) for v,ci in zip(values,intervals)],[max(0,ci[1]-v) for v,ci in zip(values,intervals)]]
        else:errors=None
        ax.bar(methods,values,yerr=errors,capsize=4)
    axes[0].set(ylabel='Verified severe failure rate',title='Tail-risk screen (scenario bootstrap 95% CI)')
    axes[1].set(ylabel='Planning ms / task',title='Measured overhead (scenario bootstrap 95% CI)')
    for ax in axes:ax.tick_params(axis='x',rotation=25);ax.grid(axis='y',alpha=.2)
    finish(fig,'v06-risk-planning')
horizon=value['experiments'].get('horizon_reliability',{}).get('runs',{})
if horizon:
    fig,axes=plt.subplots(1,2,figsize=(11,4.5))
    for strategy in ('open_loop','periodic','error_triggered','uncertainty_triggered'):
        rows=sorted([(int(k.split('-')[0][1:]),v) for k,v in horizon.items() if k.endswith('-'+strategy)])
        if not rows:continue
        for ax,field in zip(axes,('success_rate','tool_calls')):
            ax.plot([h for h,r in rows],[r[field] if field!='success_rate' or r.get('label_coverage')==1 else None for h,r in rows],marker='o',label=strategy)
            ax.set_xscale('log',base=2);ax.set_xlabel('Task horizon');ax.grid(alpha=.2)
    axes[0].set(ylabel='Verified task success',ylim=(-.05,1.05),title='Open-loop vs feedback (matched trials)')
    axes[1].set(ylabel='Real tool calls / task attempt',title='Feedback cost (includes censored attempts)')
    incomplete=horizon.get('h32-uncertainty_triggered',{})
    comparisons=value['experiments'].get('horizon_reliability',{}).get('comparisons',[])
    groups=max((c['independent_groups'] for c in comparisons if type(c.get('independent_groups')) in (int,float)),default='unknown')
    note=f"H32 uncertainty-triggered: {incomplete.get('labeled_count','unknown')}/{incomplete.get('task_count','unknown')} labeled; incomplete points excluded. Independent groups: {groups}."
    axes[0].legend(fontsize=8);finish(fig,'v06-horizon',note)
print('Wrote figures from persisted public experiment values')
