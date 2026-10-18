"""Independent real and predicted dynamics for a synthetic reliability workflow.

All resources are in-memory. No shell/network/database/payment capability exists.
The model does not receive the actual environment RNG or future observations.
"""
import copy
from dataclasses import asdict
import random
from ...benchmark.contracts import BenchmarkTask, BenchmarkResult
from ...planning.contracts import CandidateAction, PredictionDistribution, ident
from ...regression.predicates import schema_matches
from ..calibration.schemas import VerifiedOutcome, now
from ..calibration.schemas import SuccessForecast
from ...serialization import digest

ENVIRONMENT = 'reliability-workflow.v1'
TOOL_VERSION = 'workflow-tools.v1'
BENCHMARK_VERSION = 'reliability-tasks.v1'
SCHEMAS = {
    'workflow.step': {'type':'object','properties':{'route':{'type':'integer','minimum':0,'maximum':7}},'required':['route'],'additionalProperties':False},
    'workflow.repair': {'type':'object','properties':{},'additionalProperties':False}}


def action(route=0, repair=False):
    name='workflow.repair' if repair else 'workflow.step'
    return CandidateAction('repair' if repair else 'route-'+str(route),'tool',{} if repair else {'route':route},name,
                           metadata={'provenance':'synthetic_rule_policy','tool_schema_version':TOOL_VERSION})


def policy(context):
    s=context.state
    if s['route'] is None:return [action(i) for i in range(8)]
    values=[action(s['route']),action(repair=True)]
    return list(reversed(values)) if s['health']<1.5 else values


def continuation(context):
    # Fixed continuation prevents future hidden-state feedback in an open-loop plan.
    return [action(context.state['route'] or 0)]


def forecast_builder(model,method,horizon):
    """Domain-specific heuristic forecasts stay outside the generic planner."""
    def build(c,a,metrics,cutoff):
        confidence=max(.5,min(.99,.98-.015*c.state['family']-.01*a.action_payload.get('route',0)))
        values=[('raw_agent',confidence)]
        if metrics['success_probability'] is not None:values.append(('world_model_mc',metrics['success_probability']))
        return [asdict(SuccessForecast(ident(),c.benchmark_run_id,c.task_id,c.trajectory_id,c.task_group,
            'probabilistic-planner.v1:'+method,model.version,BENCHMARK_VERSION,c.environment_version,TOOL_VERSION,p,now(),cutoff,
            source=source,prediction_horizon=horizon,epistemic_uncertainty=metrics['epistemic_uncertainty'],
            provenance={'synthetic':True,'scenario_fingerprint':digest([c.task_group,c.state['domain'],c.state['family']]),
                'action_key':a.key,'source':'before_first_real_action'})) for source,p in values]
    return build


class WorkflowEnvironment:
    def __init__(self,task):
        self.task=task;self.version=0;self.history=[];self.calls=0;self.cost=0.;self.reward=0.
        m=task.metadata
        self.rng=random.Random(m['environment_seed'])
        self.shift=m['distribution']=='shift'
        self.state={'progress':0,'health':2.5,'route':None,'terminal':False,'success':None,
                    'target':m['length'],'family':m['family'],'domain':m['domain'],'severe':False}

    def snapshot(self):
        return {'state':copy.deepcopy(self.state),'history':copy.deepcopy(self.history[-8:]),
                'version':self.version,'environment_version':ENVIRONMENT+('-shift' if self.shift else '')}

    def authorize(self,a,version):
        if version!=self.version or self.state['terminal'] or a.tool_name not in SCHEMAS or not schema_matches(a.action_payload,SCHEMAS[a.tool_name]):return False
        return a.tool_name=='workflow.repair' or self.state['route'] is None or a.action_payload['route']==self.state['route']

    def is_irreversible(self,a):return False

    def execute_action(self,a):
        if not self.authorize(a,self.version):raise ValueError('Synthetic execution authorization denied')
        before=copy.deepcopy(self.state);s=self.state;self.calls+=1;self.version+=1
        severe=False
        if a.tool_name=='workflow.repair':
            s['health']=2.5;reward=-.35;cost=.002
        else:
            route=a.action_payload['route'];s['route']=route;s['progress']+=1
            # Real dynamics intentionally implemented separately from the predictor.
            if s['domain']=='risk':
                hazards=[.055+.012*s['family'],.0,.018,.035,.009,.025,.004,.012]
                hazard=hazards[route]*(2.5 if self.shift else 1)
                severe=self.rng.random()<hazard
                reward=[1.3,.3,.65,.9,.5,.8,.35,.6][route]
            else:
                severe=False
                if self.rng.random()<(.5 if self.shift else .35):s['health']-=1.
                reward=.3
            if severe:s.update(terminal=True,success=False,severe=True);reward=-12.
            elif s['progress']>=s['target']:
                s.update(terminal=True,success=s['health']>=1.)
                reward+=2. if s['success'] else -3.
            cost=.001
        if self.calls>=2*s['target']+4 and not s['terminal']:s.update(terminal=True,success=False)
        delta={k:v for k,v in s.items() if before.get(k)!=v}
        obs={'status':'completed','error_category':None,'state_delta':delta,'terminal':s['terminal'],'success':s['success'],
             'changed_resources':[],'side_effects':'memory_only','reward':reward,'cost_usd':cost,
             'unsafe':False,'severity':'high' if severe else 'none'}
        self.cost+=cost;self.reward+=reward
        self.history.append({'action':asdict(a),'observation':copy.deepcopy(obs)})
        return obs


class WorkflowWorldModel:
    model_id='synthetic-workflow-model'
    def __init__(self,rare_failure_scale=1.):
        if rare_failure_scale not in (1.,.1):raise ValueError('Use a versioned normal or underestimated-tail predictor')
        self.scale=rare_failure_scale
        self.version='workflow-predictor.v1' if rare_failure_scale==1 else 'workflow-predictor-underestimated.v1'

    async def predict(self,context,a,config):
        if context.environment_version not in (ENVIRONMENT,ENVIRONMENT+'-shift'):raise ValueError('Unsupported synthetic environment version')
        if a.tool_name not in SCHEMAS or not schema_matches(a.action_payload,SCHEMAS[a.tool_name]):raise ValueError('Invalid action schema')
        s=context.state
        if s['terminal']:raise ValueError('Terminal state cannot execute another action')
        if a.tool_name=='workflow.repair':branches=[(1.,False,0.)]
        elif s['domain']=='risk':
            route=a.action_payload['route']
            rates=[.055+.012*s['family'],0.,.018,.035,.009,.025,.004,.012]
            p=rates[route]*self.scale  # shift deliberately not corrected by the model
            branches=[(1-p,False,0.),(p,True,0.)] if p else [(1.,False,0.)]
        else:branches=[(.8,False,0.),(.2,False,1.)]
        outcomes=[]
        for p,severe,drift in branches:
            after=copy.deepcopy(s)
            if a.tool_name=='workflow.repair':after['health']=2.5;reward=-.35;cost=.002
            else:
                route=a.action_payload['route'];after.update(route=route,progress=s['progress']+1,health=s['health']-drift)
                reward=[1.3,.3,.65,.9,.5,.8,.35,.6][route] if s['domain']=='risk' else .3
                cost=.001
                if severe:after.update(terminal=True,success=False,severe=True);reward=-12.
                elif after['progress']>=after['target']:
                    after.update(terminal=True,success=after['health']>=1.)
                    reward+=2. if after['success'] else -3.
            delta={k:v for k,v in after.items() if s.get(k)!=v}
            epistemic=.08 if s['domain']=='risk' else min(.8,.08+.025*s['progress'])
            obs={'status':'completed','error_category':None,'state_delta':delta,'terminal':after['terminal'],'success':after['success'],
                 'changed_resources':[],'side_effects':'memory_only','reward':reward,'cost_usd':cost,'unsafe':False,
                 'severity':'high' if severe else 'none'}
            outcomes.append({'observation':obs,'state_delta':delta,'probability':p,'terminal':after['terminal'],
                             'success':after['success'],'success_probability':float(after['success']) if after['terminal'] else None,
                             'reward':reward,'cost_usd':cost,'uncertainty':epistemic})
        return PredictionDistribution(ident(),a.candidate_id,outcomes,self.model_id,self.version,
            calibration_status='uncalibrated',metadata={'model_calls':0,'seed':config.seed,'uncertainty_kind':'epistemic_heuristic',
                'probability_provenance':'declared_synthetic_transition_model','tool_schema_version':TOOL_VERSION})


class WorkflowAdapter:
    name='probabilistic-workflow'
    def __init__(self,tasks):self.tasks=tasks
    def load_tasks(self):return self.tasks
    def prepare_environment(self,task,capture):return WorkflowEnvironment(task)
    def final_state(self,task,environment):
        return {'state':copy.deepcopy(environment.state),'cost_usd':environment.cost,'reward':environment.reward,'calls':environment.calls}
    def cleanup(self):pass
    def evaluate(self,task,trajectory,final_state):
        success=final_state['state']['success'] is True
        sessions=trajectory.get('metadata',{}).get('planning',{}).get('sessions',[])
        forecasts=[f for s in sessions for f in s.get('research',{}).get('forecasts',[])]
        label=VerifiedOutcome(trajectory['metadata']['benchmark']['run_id'],task.id,trajectory['id'],task.metadata['task_group'],now(),
            success if final_state['state']['terminal'] else None,BENCHMARK_VERSION,
            ENVIRONMENT+('-shift' if task.metadata['distribution']=='shift' else ''),TOOL_VERSION,
            status='verified' if final_state['state']['terminal'] else 'censored')
        return BenchmarkResult(task.id,success,score=float(success),cost_usd=final_state['cost_usd'],
            failure_category=None if success else 'wrong_answer' if final_state['state']['terminal'] else 'agent_error',metadata={'synthetic':True,
                'probabilistic':{'schema_version':'research-evidence.v1','forecasts':forecasts,'outcome':asdict(label),
                    'trial_seed':task.metadata['trial_seed'],'split':task.metadata['split'],
                    'task_family':task.metadata['task_family'],'distribution':task.metadata['distribution'],
                    'cumulative_reward':final_state['reward'],'severe_failure':final_state['state']['severe'],
                    'replanning_events':[s['research']['replanning_event'] for s in sessions if s.get('research',{}).get('replanning_event')],
                    'actual_cost_usd':final_state['cost_usd']}})


def make_tasks(count=40,seeds=(0,1,2),length=3,domain='risk',include_splits=False):
    tasks=[]
    for i in range(count):
        family=i%6
        split=('train' if family<3 else 'validation' if family==3 else 'test' if family==4 else 'shift') if include_splits else 'test'
        for seed in seeds:
            group=f'{domain}-scenario-{i}'
            tasks.append(BenchmarkTask(f'{group}-seed-{seed}',f'Complete the synthetic {domain} workflow safely',
                metadata={'task_group':group,'task_family':f'family-{family}','family':family,'trial_seed':seed,
                          'environment_seed':seed*100003+i*1009,'length':length,'domain':domain,
                          'distribution':'shift' if include_splits and split=='shift' else 'in_distribution','split':split},
                tags=['synthetic',domain,f'family-{family}'],timeout_seconds=max(30,length*3)))
    return tasks
