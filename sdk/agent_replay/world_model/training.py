"""CPU categorical maximum-likelihood next-observation baseline; no neural claims."""
import copy
from collections import Counter,defaultdict
from dataclasses import asdict
import math
from pathlib import Path
from statistics import mean
from ..planning.contracts import PredictionDistribution,ident
from ..serialization import digest,canonical
from ..storage import read,save
from ..rollout.artifacts import seal_checkpoint,verify_checkpoint
from .dataset import load


def feature(state,action,environment):
    amount=action.get('action_payload',{}).get('amount_minor')
    return canonical([environment,action['tool_name'],state.get('lookup_complete'),state.get('approval_verified'),
        state.get('remaining_steps'),amount<=min(state.get('requested_minor',0),state.get('approved_minor',0)) if type(amount) is int else None])


def template(value):
    if isinstance(value,dict):return {k:({'$binding':'order_id'} if k=='order_id' else {'$binding':'amount_minor'} if k=='amount_minor' else template(v)) for k,v in value.items()}
    if isinstance(value,list):return [template(v) for v in value]
    return value


def bind(value,context,action):
    if isinstance(value,dict):
        if set(value)=={'$binding'}:
            if value['$binding']=='order_id':return context.state['order_id']
            if value['$binding']=='amount_minor':return action.action_payload['amount_minor']
            raise ValueError('Unknown structured binding')
        return {k:bind(v,context,action) for k,v in value.items()}
    if isinstance(value,list):return [bind(v,context,action) for v in value]
    return value


def train(dataset,output):
    manifest,rows=load(dataset,'train')
    if not rows:raise ValueError('Empty training dataset')
    if any(not r.get('provenance',{}).get('training_authorized') for r in rows):raise ValueError('Training consent missing')
    root=Path(output);root.mkdir(parents=True,exist_ok=False);buckets=defaultdict(Counter)
    for row in rows:
        key=feature(row['history_context'],row['action'],row['environment_version'])
        buckets[key][canonical(template(row['actual_observation']))]+=1
    table={k:[{'observation':__import__('json').loads(v),'count':n} for v,n in sorted(counts.items())] for k,counts in sorted(buckets.items())}
    loss=mean(-math.log(buckets[feature(r['history_context'],r['action'],r['environment_version'])][canonical(template(r['actual_observation']))]/sum(buckets[feature(r['history_context'],r['action'],r['environment_version'])].values())) for r in rows)
    model={'schema_version':'tabular-world-model.v1','model_id':'cpu-supervised-transition','version':digest(table),
        'method':'categorical_maximum_likelihood','table':table,'dataset_version':manifest['version'],
        'training_digest':digest(rows),'training_groups':sorted({r['task_group'] for r in rows}),
        'metrics':{'negative_log_likelihood':loss,'examples':len(rows),'epochs':1},
        'limitations':['Hand-designed structured features; categorical baseline, not an LLM or SFT result','No prediction outside observed feature support']}
    save(root/'model.json',model);save(root/'training.json',{'dataset_version':manifest['version'],'method':model['method'],'seed':0,'metrics':model['metrics']})
    reference=seal_checkpoint(root);return {'checkpoint':reference,'model_version':model['version'],'metrics':model['metrics']}


class TabularWorldModel:
    def __init__(self,reference,checkpoint_root):
        directory=verify_checkpoint(reference,checkpoint_root);self.value=read(directory/'model.json')
        if self.value.get('schema_version')!='tabular-world-model.v1':raise ValueError('Unknown world-model checkpoint')
        self.model_id=self.value['model_id'];self.version=self.value['version']
    async def predict(self,context,action,config):
        key=feature(context.state,asdict(action),context.environment_version);entries=self.value['table'].get(key)
        if not entries:
            from .interfaces import UnsupportedPrediction
            raise UnsupportedPrediction('missing_context')
        if len(entries)>8:raise ValueError('Learned distribution exceeds prediction bound')
        total=sum(e['count'] for e in entries);outcomes=[]
        for item in entries:
            observation=bind(copy.deepcopy(item['observation']),context,action);terminal=observation.get('terminal',False);success=observation.get('success')
            outcomes.append({'observation':observation,'state_delta':observation.get('state_delta',{}),'terminal':terminal,'success':success,
                'success_probability':float(success) if terminal and type(success) is bool else None,
                'probability':item['count']/total,'uncertainty':min(1,1/math.sqrt(total)),
                'reward':observation.get('reward',0),'cost_usd':observation.get('cost_usd',0)})
        return PredictionDistribution(ident(),action.candidate_id,outcomes,self.model_id,self.version,'empirical_uncalibrated',metadata={'checkpoint_dataset':self.value['dataset_version']})
