"""Synthetic Refund execution adapter and independent deterministic prediction oracle."""
import copy
from dataclasses import asdict
from ..demo import lookup, refund
from ..planning.contracts import CandidateAction, PredictionDistribution, ident
from ..planning.core import CandidateGenerator, Planner, PlanningAgent
from ..benchmark.contracts import BenchmarkTask, BenchmarkResult
from .interfaces import UnsupportedPrediction

ENVIRONMENT='refund-memory.v1'
SCHEMAS={name:{'type':'object','properties':{},'additionalProperties':False} for name in
         ('orders.lookup','approvals.check','request_information','escalate','stop')}
SCHEMAS['payments.refund']={'type':'object','properties':{'amount_minor':{'type':'integer','minimum':1}},'required':['amount_minor'],'additionalProperties':False}


def action(name,payload=None): return CandidateAction(name+':'+str((payload or {}).get('amount_minor','')), 'tool',payload or {},name,metadata={'tool_schema':SCHEMAS[name]})


def policy(context):
    s=context.state
    if not s['lookup_complete']: return [action('orders.lookup'),action('request_information'),action('escalate')]
    if not s['approval_verified']: return [action('approvals.check'),action('request_information'),action('stop')]
    return [action('payments.refund',{'amount_minor':min(s['requested_minor'],s['approved_minor'])}),
            action('payments.refund',{'amount_minor':s['requested_minor']}),action('escalate')]


class RefundEnvironment:
    def __init__(self,task):
        self.task=task;self.version=0;self.calls=[];self.history=[];self.events=[];self.refunded=False
        self.drift=task.metadata.get('context_drift',False)
        self.state={'order_id':task.id,'requested_minor':task.metadata['requested_minor'],'approved_minor':3000,
            'lookup_complete':False,'approval_verified':False,'refresh_seen':False,
            'terminal':False,'success':None,'remaining_steps':6}
    def snapshot(self): return {'state':copy.deepcopy(self.state),'history':copy.deepcopy(self.events),'version':self.version,'environment_version':ENVIRONMENT}
    def is_irreversible(self,a):return a.tool_name=='payments.refund'
    def irreversible_key(self,a):return 'refund:'+self.state['order_id'] if self.is_irreversible(a) else a.key
    def authorize(self,a,version):
        if version!=self.version or self.state['terminal'] or a.tool_name not in SCHEMAS: return False
        from ..regression.predicates import schema_matches
        if not schema_matches(a.action_payload,SCHEMAS[a.tool_name]): return False
        if a.tool_name=='payments.refund':
            return (not self.refunded and self.state['lookup_complete'] and self.state['approval_verified'] and
                0<a.action_payload['amount_minor']<=min(self.state['approved_minor'],self.state['requested_minor']))
        return True
    def execute_action(self,a):
        if not self.authorize(a,self.version): raise ValueError('Refund execution policy denied')
        before=copy.deepcopy(self.state);s=self.state;self.calls.append(a.tool_name);self.version+=1
        status='completed';error=None;value={}
        if a.tool_name=='orders.lookup':
            if self.drift and not s['refresh_seen']:
                s['refresh_seen']=True;status='error';error='stale_context'
            else:
                # Outer execution span owns capture; reuse the fixture implementation once.
                value=lookup.__wrapped__(s['order_id']);s['lookup_complete']=True;s['approved_minor']=value['approved_minor']
        elif a.tool_name=='approvals.check': s['approval_verified']=bool(s['lookup_complete'])
        elif a.tool_name=='payments.refund':
            value=refund.__wrapped__(s['order_id'],a.action_payload['amount_minor']);self.refunded=True;s.update(terminal=True,success=True)
        else: s.update(terminal=True,success=False)
        s['remaining_steps']-=1
        if s['remaining_steps']==0 and not s['terminal']:s.update(terminal=True,success=False)
        out={'status':status,'error_category':error,'state_delta':{k:v for k,v in s.items() if before[k]!=v},
            'changed_resources':['refund'] if a.tool_name=='payments.refund' else [],'side_effects':'memory_only',
            'terminal':s['terminal'],'success':s['success'],'reward':float(s['success'] is True),
            'cost_usd':.002 if a.tool_name=='payments.refund' else .001,'value':value,'unsafe':False}
        self.history.append(out);self.events.append({'action':asdict(a),'observation':copy.deepcopy(out)});return out


class DeterministicRefundModel:
    model_id='refund-oracle';version='refund-rules.v1'
    async def predict(self,context,a,config):
        if context.environment_version!=ENVIRONMENT: raise UnsupportedPrediction('environment_mismatch')
        from ..regression.predicates import schema_matches
        if a.tool_name not in SCHEMAS or not schema_matches(a.action_payload,SCHEMAS[a.tool_name]): raise ValueError('Unsupported action')
        s=context.state;delta={};terminal=False;success=None;value={};status='completed';error=None
        if a.tool_name=='orders.lookup':
            delta={'lookup_complete':True}
            value={'order_id':s['order_id'],'approved_minor':3000,'paid_minor':29900}
        elif a.tool_name=='approvals.check':
            if s['lookup_complete']: delta={'approval_verified':True}
        elif a.tool_name=='payments.refund':
            safe=s['lookup_complete'] and s['approval_verified'] and 0<a.action_payload['amount_minor']<=min(s['requested_minor'],s['approved_minor'])
            terminal=True;success=bool(safe);delta={'terminal':True,'success':success}
            if safe:value={'ok':True,'simulated':True,'amount_minor':a.action_payload['amount_minor']}
            else:status='denied';error='policy_violation'
        else: terminal=True;success=False;delta={'terminal':True,'success':False}
        delta['remaining_steps']=s['remaining_steps']-1
        if delta['remaining_steps']<=0 and not terminal:terminal=True;success=False;delta.update(terminal=True,success=False)
        delta={k:v for k,v in delta.items() if s.get(k)!=v}
        cost=.002 if a.tool_name=='payments.refund' else .001
        observation={'status':status,'error_category':error,'state_delta':delta,'changed_resources':['refund'] if a.tool_name=='payments.refund' else [],
            'side_effects':'memory_only','terminal':terminal,'success':success,'reward':float(success is True),'cost_usd':cost,'value':value,'unsafe':False}
        return PredictionDistribution(ident(),a.candidate_id,[{'observation':observation,'state_delta':delta,'terminal':terminal,
            'success':success,'success_probability':float(success) if terminal else None,'probability':1.,'uncertainty':0.,
            'reward':float(success) if terminal else 0.,'cost_usd':cost}],self.model_id,self.version,
            metadata={'seed':config.seed,'sampling':'deterministic','tool_schema_version':'refund-tools.v1'})


class RefundBaseline:
    def choose_action(self,c):
        return action('payments.refund',{'amount_minor':c.state['requested_minor']}) if c.state['lookup_complete'] and c.state['approval_verified'] else policy(c)[0]
    async def run(self,task,environment,capture):
        from ..planning.contracts import AgentContext
        for _ in range(6):
            snap=environment.snapshot()
            if snap['state']['terminal']:break
            c=AgentContext(task.id,task.metadata['task_group'],task.instruction,snap['state'],snap['version'],ENVIRONMENT,snap['history'])
            a=self.choose_action(c)
            if not environment.authorize(a,c.state_version):break
            with capture.span(a.tool_name,kind='tool',input={'action':asdict(a),'state':c.state,'environment_version':ENVIRONMENT,'task_group':c.task_group,
                    'tool_schema_version':'refund-tools.v1','training_authorized':True}) as span:
                actual=span.set_output(environment.execute_action(a))
            if actual.get('status')=='error':break
        return environment.state


class RefundAdapter:
    name='predictive-refund'
    def __init__(self,tasks):self.tasks=tasks
    def load_tasks(self):return self.tasks
    def prepare_environment(self,task,capture):
        capture.trace['metadata']['world_model']={'training_authorized':True,'scenario_group':task.metadata['task_family']}
        return RefundEnvironment(task)
    def final_state(self,task,environment):return {'state':environment.state,'cost':sum(o['cost_usd'] for o in environment.history)}
    def evaluate(self,task,trajectory,final_state):
        success=final_state['state']['success'] is True
        return BenchmarkResult(task.id,success,cost_usd=final_state['cost'],failure_category=None if success else 'wrong_answer',metadata={'synthetic':True})
    def cleanup(self):pass


def tasks(count=20,prefix='refund'):
    return [BenchmarkTask(f'{prefix}-{i}','Refund only the approved amount',
        metadata={'task_group':f'{prefix}-scenario-{i}','task_family':f'{prefix}-family-{i//2}',
            'requested_minor':[2900,29900,1500,3500][i%4],'context_drift':i%5==0},tags=['synthetic','refund'],timeout_seconds=20) for i in range(count)]
