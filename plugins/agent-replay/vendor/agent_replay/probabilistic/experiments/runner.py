"""CPU research orchestration reuses BenchmarkRunner, RunStore and PlanningAgent."""
import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time
import uuid
from ...benchmark.runner import run_benchmark
from ...benchmark.contracts import AgentConfig
from ...benchmark.reports import write_reports
from ...benchmark.compare import compare_runs
from ...planning.core import PlanningAgent
from ...serialization import digest
from ...storage import save, read
from .config import ExperimentConfig, from_dict
from .domain import WorkflowAdapter, WorkflowWorldModel, make_tasks, BENCHMARK_VERSION, ENVIRONMENT, TOOL_VERSION
from .statistics import paired_effect, holm
from ..planning.rollout_sampler import SamplingConfig, ResearchBudget
from ..planning.risk_aware_planner import RiskAwarePlanner
from ..planning.risk_metrics import cvar
from ..horizon.replanning_policy import ReplanningPolicy
from ..horizon.reliability_analyzer import analyze
from ..calibration.collector import join_outcomes
from ..calibration.evaluator import evaluate_calibration
from ..calibration.schemas import now

METHODS=('greedy','expected_value','mean_variance','cvar','uncertainty_cvar')
STRATEGIES=('open_loop','periodic','error_triggered','uncertainty_triggered')


def compare_metric(baseline,candidate,field,seed,min_groups):
    if any(r.get('label_status')!='verified' for r in baseline+candidate):
        return {'decision':'INCONCLUSIVE','effect':None,'confidence_interval':None,'p_value':1.,
                'paired_samples':0,'independent_groups':0,'reason':'Required tasks are censored or missing verified labels'}
    return paired_effect(baseline,candidate,field,seed,min_groups)


def _run(root,name,tasks,config,method='expected_value',horizon=None,strategy='periodic',budget=None):
    target=root/name
    sampling=SamplingConfig(config.candidate_count,config.samples,horizon or config.horizon,config.seed)
    replan=ReplanningPolicy(strategy,config.replan_interval,config.error_threshold,config.uncertainty_threshold)
    model=WorkflowWorldModel(config.rare_failure_scale)
    planner=RiskAwarePlanner(model,sampling,target,method,config.risk_weight,config.alpha,config.uncertainty_weight,replan,budget)
    run,results=run_benchmark(WorkflowAdapter(tasks),PlanningAgent(None,planner),
        AgentConfig('research-'+method,model=model.model_id,parameters={'planning_mode':'active',
            'research':asdict(config),'sampling':asdict(sampling),'replanning':asdict(replan)}),target)
    if budget and budget.exhausted:raise ValueError('Shared experiment resource budget exhausted')
    write_reports(target)
    rows=[];forecasts=[];outcomes=[]
    for result in results:
        e=result.metadata.get('probabilistic')
        if not e:raise ValueError('Required benchmark verifier evidence missing')
        p=result.metadata['planning']
        forecasts.extend(e['forecasts']);outcomes.append(e['outcome'])
        rows.append({'task_id':result.task_id,'task_group':result.metadata['task_group'],'trial_seed':e['trial_seed'],
            'trajectory_id':result.trajectory_id,'trajectory_path':result.trajectory_path,
            'success':result.success if e['outcome']['status']=='verified' else None,'label_status':e['outcome']['status'],'cumulative_reward':e['cumulative_reward'],
            'severe_failure':e['severe_failure'] if e['outcome']['status']=='verified' else None,'tool_calls':result.tool_call_count,'cost_usd':result.cost_usd,
            'planning_latency_ms':p['planning_latency_ms'],'duration_ms':result.duration_ms,'planning_tokens':p['planning_tokens'],
            'prediction_calls':p['prediction_calls'],'replans':max(0,len(e['replanning_events'])-1),
            'split':e['split'],'task_family':e['task_family'],'distribution':e['distribution']})
    joined=join_outcomes(forecasts,outcomes)
    indexed={(r.task_id):r.metadata['probabilistic'] for r in results}
    for row in joined['rows']+joined['unlabeled']:
        extra=indexed[row['task_id']]
        row.update(split=extra['split'],trial_seed=extra['trial_seed'],task_family=extra['task_family'],distribution=extra['distribution'])
    forecasts_by_task={}
    for r in joined['rows']:
        if r['source']=='world_model_mc':forecasts_by_task[r['task_id']]=(r['probability']-r['outcome'])**2
    for row in rows:row['brier_score']=forecasts_by_task.get(row['task_id'])
    from statistics import mean
    verified=[r for r in rows if r['label_status']=='verified']
    summary={'success_rate':mean(r['success'] for r in verified) if verified else None,'task_count':len(rows),
        'labeled_count':len(verified),'label_coverage':len(verified)/len(rows),
        'severe_failure_rate':mean(r['severe_failure'] for r in verified) if verified else None,
        'empirical_loss_cvar':cvar([-r['cumulative_reward'] for r in verified],config.alpha) if verified else None,
        **{name:mean(r[name] for r in rows) if all(r[name] is not None for r in rows) else None for name in
            ('cumulative_reward','cost_usd','tool_calls','duration_ms','planning_latency_ms','planning_tokens','prediction_calls','replans')}}
    evidence={'schema_version':'research-evidence.v1','run_id':run.id,'benchmark_version':BENCHMARK_VERSION,
        'suite_digest':run.suite_digest,'world_model_version':model.version,'evaluation_protocol':'paired-synthetic-workflow.v1',
        'synthetic':True,'rows':rows,'forecasts':joined,'summary':summary,'sampling_config':asdict(sampling),
        'replanning_policy':asdict(replan),'planner':method}
    save(target/'reliability.json',evidence)
    from .failure_analysis import analyze_failures
    save(target/'failure-analysis.json',analyze_failures(target,results))
    first=next((p for p in sorted((target/'research').glob('*.json'))),None)
    example=None
    if first:
        example_record=read(first,limit=16*1024*1024)
        example={'selected_candidate_id':example_record['selected_candidate_id'],
                 'candidate_evaluations':example_record['candidate_evaluations']}
    return {'run_id':run.id,'path':name,'summary':summary,'rows':rows,'forecasts':joined,'candidate_example':example},model


def _run_experiment(experiment,output,config=None):
    if experiment not in ('calibration','risk_planning','horizon_reliability'):raise ValueError('Unknown experiment')
    config=config or ExperimentConfig()
    root=Path(output);root.mkdir(parents=True,exist_ok=False)
    started=time.perf_counter()
    try:sha=subprocess.run(['git','rev-parse','HEAD'],capture_output=True,text=True,timeout=2,check=True).stdout.strip()
    except (OSError,subprocess.SubprocessError):sha='unavailable'
    import hashlib
    package_root=Path(__file__).resolve().parents[2]
    code_hash=digest({p.relative_to(package_root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(package_root.rglob('*.py'))})
    provenance={'experiment_id':str(uuid.uuid4()),'experiment':experiment,'created_at':now(),'git_commit_sha':sha,
        'benchmark_version':BENCHMARK_VERSION,'environment_version':ENVIRONMENT,'tool_version':TOOL_VERSION,
        'agent_version':'probabilistic-planner.v1','model_version':WorkflowWorldModel(config.rare_failure_scale).version,
        'config':asdict(config),'config_digest':digest(asdict(config)),'python_source_digest':code_hash,
        'python':platform.python_version(),'synthetic':True,'status':'running',
        'inference':{'backend':'CPU_distribution_rules','token_usage':0,'external_model_calls':0,'provider_cost_usd':0},
        'nondeterminism':['wall-clock latency, UUIDs and timestamps; actual transitions and simulations use separate recorded PRNG seeds'],
        'dataset_split':'scenario/task-family grouped; no trajectory steps split across sets'}
    save(root/'experiment.json',{'schema_version':'research-experiment.v1',**provenance})
    runs={};comparisons=[];calibration=None;horizons={}
    budget=ResearchBudget(config.max_total_predictions,config.max_experiment_seconds,config.max_artifact_mb*1024*1024)
    def execute(name,tasks,cfg=config,**kwargs):
        if len(runs)>=config.max_runs:raise ValueError('Experiment run budget exhausted')
        result,model=_run(root,name,tasks,cfg,budget=budget,**kwargs)
        if sum(p.stat().st_size for p in root.rglob('*') if p.is_file())>config.max_artifact_mb*1024*1024:
            raise ValueError('Experiment storage limit exhausted')
        runs[name]=result;return result,model
    if experiment=='calibration':
        tasks=make_tasks(config.tasks,config.seeds,length=1,include_splits=True)
        result,_=execute('forecasts',tasks,method='greedy',horizon=1)
        calibration=evaluate_calibration(result['forecasts']['rows'],config.seed,config.min_groups)
        save(root/'calibration.json',calibration)
    elif experiment=='risk_planning':
        tasks=make_tasks(config.tasks,config.seeds,length=config.horizon)
        for method in METHODS:execute(method,tasks,method=method)
        baseline=runs['expected_value']['rows']
        for method in METHODS:
            if method=='expected_value':continue
            for field in ('success','severe_failure','cumulative_reward','cost_usd','duration_ms'):
                comparisons.append({'candidate':method,'baseline':'expected_value','metric':field,
                    **compare_metric(baseline,runs[method]['rows'],field,config.seed,config.min_groups)})
        if config.screening:
            # One-factor-at-a-time screen; explicit bounds, not automatic full factorial.
            variants={'k2':replace(config,candidate_count=2),'k8':replace(config,candidate_count=8),
                'm8':replace(config,samples=8),'m32':replace(config,samples=32),
                'h1':replace(config,horizon=1),'h5':replace(config,horizon=5),'h10':replace(config,horizon=10),
                'lambda0':replace(config,risk_weight=0),'lambda01':replace(config,risk_weight=.1),'lambda1':replace(config,risk_weight=1),
                'alpha08':replace(config,alpha=.8),'alpha095':replace(config,alpha=.95),
                'underestimated_tail':replace(config,rare_failure_scale=.1)}
            for name,cfg in variants.items():execute('screen-'+name,tasks,cfg,method='cvar',horizon=cfg.horizon)
        for name in runs:
            if name!='expected_value':
                comparison=compare_runs(root/'expected_value',root/name)
                # Keep full task/regression links in the existing report format.
                save(root/name/'reports/comparison.json',comparison)
    else:
        if len(config.horizons)*len(STRATEGIES)>config.max_runs:raise ValueError('Horizon grid exceeds run budget')
        for horizon in config.horizons:
            tasks=make_tasks(config.tasks,config.seeds,length=horizon,domain='horizon')
            for strategy in STRATEGIES:
                name=f'h{horizon}-{strategy}'
                result,model=execute(name,tasks,method='expected_value',horizon=horizon,strategy=strategy)
                analysis=analyze(root/name,model,config.horizons,strategy)
                save(root/name/'horizon.json',analysis);horizons[name]=analysis
                if strategy!='open_loop':
                    for field in ('success','cost_usd','duration_ms'):
                        comparisons.append({'candidate':name,'baseline':f'h{horizon}-open_loop','metric':field,
                            **compare_metric(runs[f'h{horizon}-open_loop']['rows'],result['rows'],field,config.seed,config.min_groups)})
    if comparisons:
        for comparison,p in zip(comparisons,holm([c['p_value'] for c in comparisons])):
            comparison['holm_p_value']=p
            if p>.05:comparison['decision']='INCONCLUSIVE'
    size=sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
    # Large per-task/prediction evidence stays in the referenced run sidecars.
    compact_runs={name:{k:v for k,v in record.items() if k not in ('rows','forecasts')} for name,record in runs.items()}
    compact_horizons={name:{k:v for k,v in record.items() if k!='records'} for name,record in horizons.items()}
    result={'schema_version':'research-experiment.v1',**provenance,'status':'completed','finished_at':now(),
        'runs':compact_runs,'comparisons':comparisons,'calibration':calibration,'horizon_evaluations':compact_horizons,
        'performance':{'elapsed_seconds':time.perf_counter()-started,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'artifact_bytes':size,'artifact_bytes_per_task_execution':size/sum(r['summary']['task_count'] for r in runs.values()),
            'task_executions':sum(r['summary']['task_count'] for r in runs.values()),'provider_cost_usd':0,
            'cost_units':'tool prices are declared synthetic fixtures; no commercial cost-efficiency claim'},
        'limitations':['Synthetic CPU workflow only; no production/general agent improvement claim.',
            'Monte Carlo tail estimates can miss rare failures; H1/H2/H3 are hypotheses, not assumed findings.',
            'Raw agent confidence and epistemic uncertainty are heuristics, not calibrated probabilities.',
            'Screening grid is one-factor-at-a-time; full factorial and external benchmarks remain operator experiments.',
            'Repeated trial seeds clustered by scenario; small samples and corrected tests often inconclusive.']}
    save(root/'experiment.json',result)
    from .reporting import write_report
    write_report(root,result)
    return result


def run_experiment(experiment,output,config=None):
    existed=Path(output).exists()
    try:return _run_experiment(experiment,output,config)
    except BaseException as exc:
        path=Path(output)/'experiment.json'
        if not existed and path.exists():
            record=read(path,limit=32*1024*1024)
            save(path,{**record,'status':'interrupted','error_code':type(exc).__name__,'finished_at':now()})
        raise


def main(argv=None):
    parser=argparse.ArgumentParser(prog='agent-replay probabilistic run')
    parser.add_argument('--experiment',choices=('calibration','risk_planning','horizon_reliability'),required=True)
    parser.add_argument('--config',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    try:
        result=run_experiment(args.experiment,args.output,from_dict(read(args.config)))
        print(json.dumps({'experiment_id':result['experiment_id'],'status':result['status'],'output':str(Path(args.output).resolve()),
                          'runs':{k:v['summary'] for k,v in result['runs'].items()},'performance':result['performance']}))
        return 0
    except (ValueError,OSError,KeyError,TypeError):
        print(json.dumps({'error':'Research run rejected or incomplete; inspect sanitized local artifacts and configuration'}),file=sys.stderr)
        return 2


if __name__=='__main__':raise SystemExit(main())
