"""Bundled deterministic in-memory benchmark, independent real/prediction models."""
import copy
from dataclasses import asdict
from .contracts import AgentContext, CandidateAction, PlanningConfig, ident, data
from .core import CandidateGenerator, Planner, PlanningAgent
from .models import MockWorldModel, ReplayWorldModel
from ..benchmark.contracts import BenchmarkTask, BenchmarkResult, AgentConfig

SCHEMAS={name:{'type':'object','properties':{},'additionalProperties':False} for name in ('deploy','run_tests','check_compatibility')}


def policy(context):
    names=['deploy','check_compatibility','run_tests'] if context.state.get('tests_run') else ['run_tests','deploy','check_compatibility']
    return [CandidateAction(name,'tool',{},name,metadata={'tool_schema':SCHEMAS[name]}) for name in names]


class DeploymentEnvironment:
    def __init__(self, task):
        self.task=task;self.version=0;self.history=[];self.calls=[]
        self.state={'terminal':False,'success':None,'tests_run':False,'compat_checked':False,
            'workload':task.metadata['workload'],'remaining_steps':task.metadata.get('max_actions',6)}
    def snapshot(self): return {'state':copy.deepcopy(self.state),'version':self.version,'environment_version':'deployment-memory.v1','history':copy.deepcopy(self.history)}
    def authorize(self, action, expected_version):
        return expected_version==self.version and action.tool_name in SCHEMAS and action.action_payload=={} and not self.state['terminal']
    def execute_action(self, action):
        if not self.authorize(action,self.version): raise ValueError('Execution denied')
        self.calls.append(action.tool_name);before=copy.deepcopy(self.state);self.version+=1
        name=action.tool_name
        if name=='check_compatibility': self.state['compat_checked']=True
        elif name=='run_tests': self.state['tests_run']=True
        else:
            valid=(not self.task.metadata['requires_compat'] or self.state['compat_checked']) and (not self.task.metadata['requires_tests'] or self.state['tests_run'])
            self.state.update(terminal=True,success=valid)
        self.state['remaining_steps']-=1
        if self.state['remaining_steps']==0 and not self.state['terminal']: self.state.update(terminal=True,success=False)
        delta={k:v for k,v in self.state.items() if before[k]!=v}
        result={'status':'completed','state_delta':delta,'changed_resources':['service'] if name=='deploy' else [],
            'side_effects':'memory_only','terminal':self.state['terminal'],'success':self.state['success'],
            'cost_usd':.02 if name=='deploy' else .01,'unsafe':name=='deploy' and not self.state['success'],
            'severity':'high' if name=='deploy' and not self.state['success'] else 'none'}
        self.history.append(result);return result


class DeploymentBaseline:
    def choose_action(self, context): return CandidateAction('deploy','tool',{},'deploy')
    async def run(self, task, environment, capture):
        snapshot=environment.snapshot();action=self.choose_action(None)
        if not environment.authorize(action,snapshot['version']): raise ValueError('Execution denied')
        with capture.span(action.tool_name,kind='tool',input={'action':asdict(action),'state':snapshot['state'],
                'environment_version':snapshot['environment_version'],'task_group':task.metadata['task_group']}) as span:
            actual=span.set_output(environment.execute_action(action))
        return actual


class DeploymentAdapter:
    name='predictive-deployment'
    def __init__(self, tasks): self.tasks=tasks
    def load_tasks(self): return self.tasks
    def prepare_environment(self, task, capture): return DeploymentEnvironment(task)
    def final_state(self, task, environment): return {'state':environment.state,'cost_usd':sum(o['cost_usd'] for o in environment.history),'calls':len(environment.calls)}
    def evaluate(self, task, trajectory, final_state):
        success=final_state['state']['success'] is True
        return BenchmarkResult(task.id,success,cost_usd=final_state['cost_usd'],
            failure_category=None if success else 'wrong_answer',metadata={'synthetic':True,'cost_kind':'synthetic_tool_usd'})
    def cleanup(self): pass


def tasks(count=20, prefix='evaluation', seed=0):
    import random
    rng=random.Random(seed)
    return [BenchmarkTask(f'{prefix}-{i}','Deploy without breaking compatibility',
        metadata={'task_group':f'{prefix}-group-{i}','seed':seed+i,'workload':round(rng.uniform(0,1),5),
                  'requires_compat':i%4 in (1,3),'requires_tests':i%4 in (2,3),
                  'max_actions':2 if i%7==0 else 6},tags=['synthetic','deployment'],timeout_seconds=20) for i in range(count)]


def agent(mode, config=None, records=None):
    baseline=DeploymentBaseline()
    if mode=='baseline': return baseline
    if mode not in ('planning','planning_with_replay_context'): raise ValueError('Invalid benchmark mode')
    world=MockWorldModel('tests-only')
    if mode=='planning_with_replay_context': world=ReplayWorldModel(records or [],fallback=world)
    return PlanningAgent(baseline,Planner(CandidateGenerator(policy,SCHEMAS),world,config or PlanningConfig(enabled=True)))
