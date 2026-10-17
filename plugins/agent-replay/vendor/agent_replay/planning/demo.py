"""Reproducible paired experiments; successes and losses are measured, not asserted."""
import asyncio
from dataclasses import asdict
from pathlib import Path
from .benchmark import tasks, agent, DeploymentAdapter, DeploymentEnvironment, SCHEMAS
from .contracts import CandidateAction, AgentContext, PredictionConfig, ident
from .dataset import extract, assert_disjoint
from .models import MockWorldModel, ReplayWorldModel
from .metrics import calibration
from .calibration import compare_versions
from ..benchmark.runner import run_benchmark
from ..benchmark.contracts import AgentConfig
from ..benchmark.compare import compare_runs
from ..benchmark.reports import write_reports
from ..benchmark.release import release_gate
from ..storage import read, save


class ExplorationAgent:
    async def run(self, task, environment, capture):
        # Fixed trusted collection policies; never selected from imagined execution.
        index=int(task.id.rsplit('-',1)[1])%3
        script=[['deploy'],['run_tests','deploy'],['check_compatibility','run_tests','deploy']][index]
        for name in script:
            snapshot=environment.snapshot()
            if snapshot['state']['terminal']: break
            action=CandidateAction(name,'tool',{},name)
            if not environment.authorize(action,snapshot['version']): raise ValueError('Collection denied')
            with capture.span(name,kind='tool',input={'action':asdict(action),'state':snapshot['state'],
                    'task_group':task.metadata['task_group'],'environment_version':snapshot['environment_version']}) as span:
                span.set_output(environment.execute_action(action))


def records_from_run(directory):
    from ..benchmark.store import RunStore
    _,results=RunStore(directory).load()
    return [row for r in results for row in extract(read(Path(directory)/r.trajectory_path))]


async def prediction_rows(model, rows):
    result=[];seen=set()
    for row in rows:
        if row['action']['tool_name']!='deploy' or row['task_group'] in seen: continue
        seen.add(row['task_group'])
        state=row['history_context'];ctx=AgentContext(row['source_trajectory_id'],row['task_group'],'Deploy',state,0,row['environment_version'])
        action=CandidateAction('deploy','tool',{},'deploy')
        try: prediction=await model.predict(ctx,action,PredictionConfig())
        except ValueError: continue
        probabilities=[o.get('probability') for o in prediction.outcomes]
        if any(p is None for p in probabilities): continue
        success=sum(o.get('success_probability',0)*o['probability'] for o in prediction.outcomes)
        result.append({'task_group':row['task_group'],'probability':success,'outcome':row['actual_observation']['success']})
    return result


def demo(output, count=20, seed=0):
    root=Path(output);root.mkdir(parents=True,exist_ok=False)
    training_tasks=tasks(60,'training',seed+100)
    run_benchmark(DeploymentAdapter(training_tasks),ExplorationAgent(),AgentConfig('trusted-collection'),root/'collection')
    historical=records_from_run(root/'collection');save(root/'transitions.json',historical)
    definitions=tasks(count,'evaluation',seed)
    summary={};runs={}
    for mode in ('baseline','planning','planning_with_replay_context'):
        target=root/mode
        run,results=run_benchmark(DeploymentAdapter(definitions),agent(mode,records=historical),
            AgentConfig('deployment-agent',parameters={'planning_mode':mode,'seed':seed}),target)
        summary[mode]={'success_rate':run.summary['success_rate'],'passed':run.summary['pass_count'],
            'failed':run.summary['fail_count'],'metrics':run.summary['distributions'],'planning':run.summary.get('planning')}
        runs[mode]=target;write_reports(target)
    comparisons={}
    for mode in ('planning','planning_with_replay_context'):
        comparison=compare_runs(runs['baseline'],runs[mode]);comparisons[mode]=comparison
        write_reports(runs[mode],comparison=comparison)
    evaluation=records_from_run(runs['planning'])
    assert_disjoint(historical,evaluation)
    before=asyncio.run(prediction_rows(MockWorldModel('tests-only'),evaluation))
    after=asyncio.run(prediction_rows(ReplayWorldModel(historical,fallback=MockWorldModel()),evaluation))
    offline=compare_versions(before,after)
    gate=release_gate(runs['baseline'],runs['planning_with_replay_context'],
        {'min_tasks':20,'critical_tags':[],'require_replay':True,'planning':{'max_total_cost_increase':.25,'max_tool_call_increase':.25}})
    result={'schema_version':'planning-experiment.v1','seed':seed,'tasks_per_mode':count,'modes':summary,
        'paired_comparisons':comparisons,'offline_prediction_comparison':offline,'quality_gate':gate,
        'real_external_operations':0,'real_model_calls':0,
        'limitations':['Synthetic in-memory dynamics and scripted candidate policy; no production reliability claim',
                      'Costs are synthetic tool USD; CPU/wall latency is measured; model tokens are zero',
                      'Task groups and exact contexts are disjoint; semantic duplicate grouping remains dataset-producer responsibility']}
    save(root/'experiment.json',result);return result
