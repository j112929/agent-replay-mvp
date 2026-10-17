"""Matched Refund ablations using the existing benchmark and report pipeline."""
import asyncio
from dataclasses import asdict
from pathlib import Path
from .refund import RefundAdapter,RefundBaseline,DeterministicRefundModel,tasks,SCHEMAS,policy
from .dataset import build,load
from .evaluation import evaluate
from .training import train,TabularWorldModel
from ..planning.contracts import PlanningConfig
from ..planning.core import Planner,PlanningAgent,CandidateGenerator
from ..benchmark.runner import run_benchmark
from ..benchmark.contracts import AgentConfig
from ..benchmark.compare import compare_runs
from ..benchmark.reports import write_reports
from ..benchmark.release import release_gate
from ..storage import read,save


def run(output,count=20,ablate=True):
    if type(count) is not int or not 12<=count<=100:raise ValueError('Use 12–100 paired tasks')
    root=Path(output);root.mkdir(parents=True,exist_ok=False)
    definitions=tasks(count);models={};summaries={};comparisons={}
    variants={'baseline':None,'safe-policy':None,'shadow':{'mode':'shadow'},'active':{'mode':'active'},'correcting':{'mode':'active','self_correction':True}}
    if ablate:variants.update({'horizon1':{'horizon':1},'horizon5':{'horizon':5},'no-risk':{'lambda_risk':0},'no-uncertainty':{'lambda_uncertainty':0}})
    for name,overrides in variants.items():
        config=PlanningConfig(enabled=overrides is not None,horizon=3,rollouts_per_candidate=2)
        if overrides is not None:
            config=PlanningConfig(**{**asdict(config),**overrides});model=DeterministicRefundModel();models[name]=model
            agent=PlanningAgent(RefundBaseline(),Planner(CandidateGenerator(policy,SCHEMAS),model,config))
        else:
            class SafePolicy(RefundBaseline):
                def choose_action(self,context):return policy(context)[0]
            agent=SafePolicy() if name=='safe-policy' else RefundBaseline()
        run_,results=run_benchmark(RefundAdapter(definitions),agent,AgentConfig('refund-'+name,parameters={'planning_mode':name,'config':asdict(config)}),root/name)
        summaries[name]=run_.summary;write_reports(root/name)
        if name!='baseline':
            comparison=compare_runs(root/'baseline',root/name);comparisons[name]=comparison;write_reports(root/name,comparison=comparison)
    gate=release_gate(root/'baseline',root/'correcting',{'min_tasks':20,'critical_tags':[],
        'planning':{'max_total_cost_increase':.25,'max_tool_call_increase':.25}})
    comparisons['correcting']['quality_gate']=gate;write_reports(root/'correcting',comparison=comparisons['correcting'])
    save(root/'quality-gate.json',gate)
    traces=[read(p) for p in (root/'correcting/trajectories').glob('*.json')]
    manifest=build(traces,root/'dataset',authorized=True)
    learned=train(root/'dataset',root/'checkpoints'/'cpu')
    model=TabularWorldModel(learned['checkpoint'],root/'checkpoints');_,heldout=load(root/'dataset','test')
    evaluations={'oracle':asyncio.run(evaluate(DeterministicRefundModel(),heldout,root/'oracle-evaluation.json')),
        'trained':asyncio.run(evaluate(model,heldout,root/'trained-evaluation.json'))}
    # Shadow validation uses fresh scenario groups, not training groups, and real baseline execution.
    cfg=PlanningConfig(enabled=True,mode='shadow',horizon=1,rollouts_per_candidate=1,max_predictions=12)
    run_benchmark(RefundAdapter(tasks(count,'shadow-heldout')),PlanningAgent(RefundBaseline(),Planner(CandidateGenerator(policy,SCHEMAS),model,cfg)),
        AgentConfig('trained-shadow',parameters={'planning_mode':'shadow'}),root/'trained-shadow')
    result={'schema_version':'world-model-experiment.v1','synthetic':True,'version':'0.5.0','task_count':count,'modes':summaries,'comparisons':comparisons,
        'dataset':manifest,'training':learned,'evaluations':evaluations,'quality_gate':gate,'external_operations':0,'model_calls':0,
        'limitations':['Synthetic Refund doubles only; no production reliability claim','Compare safe-policy without a world model to isolate deterministic rule changes from planning','CPU categorical fit is not neural SFT or LoRA','Tiny local CPU LoRA is tested separately; useful HF predictions, live providers and GPU quality remain unvalidated']}
    save(root/'experiment.json',result);return result
