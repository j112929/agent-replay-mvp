import copy
from .matcher import Matcher,matches
from .fixtures import Fixtures
from ..replay import RecordedError,ReplayMismatch
from ..project import callable_for

class Policy:
    model=None
    overrides={}
    def __init__(self,trace,spec,project):
        self.matcher=Matcher(trace['steps']); self.fixtures=Fixtures(spec.get('fixtures',[])); self.spec=spec; self.project=project; self.run=None; self.calls=0
    def raise_recorded(self,error):
        mapping=self.project.get('error_mappings',{}).get(error.get('type'))
        safe={'ValueError':ValueError,'RuntimeError':RuntimeError,'KeyError':KeyError,'TimeoutError':TimeoutError}
        if mapping in safe: raise safe[mapping](error.get('message','Recorded failure'))
        raise RecordedError(error.get('type','Error')+': '+error.get('message','Recorded failure'))
    def resolve(self,name,kind,inputs):
        event=self.run._current_event
        if len(self.run.trace['steps'])>self.spec.get('limits',{}).get('max_steps',2000): raise ReplayMismatch('Step budget exceeded')
        response=self.fixtures.resolve(event)
        if response is not None:
            event['replay_mode']='fixture'
            if response.get('kind')=='raise': self.raise_recorded(response['error'])
            if response.get('kind')!='return': raise ValueError('Invalid fixture response')
            return copy.deepcopy(response.get('value'))
        rules=[r for r in self.spec.get('tool_rules',[]) if matches(r['selector'],event)]
        if len(rules)>1: raise ReplayMismatch('Conflicting tool rules')
        if rules and rules[0]['mode']=='local':
            event['replay_mode']='local'; event['tool']['implementation_id']=rules[0]['implementation_id']
            return callable_for(self.project,'tools',rules[0]['implementation_id'])(**inputs)
        source=self.matcher.take(event); event['source_step_id']=source['id']; event['replay_mode']='recorded'
        if source.get('status')=='error': self.raise_recorded(source.get('error') or {})
        if source.get('_output_replayability','complete')!='complete': raise ReplayMismatch('Recorded output is not recoverable')
        if 'output' not in source: raise ReplayMismatch('Historical output missing')
        return copy.deepcopy(source['output'])
    def resolve_llm(self,request):
        event=self.run._current_event
        rules=[r for r in self.spec.get('model_rules',[]) if matches(r['selector'],event)]
        if len(rules)>1: raise ReplayMismatch('Conflicting model rules')
        if rules and rules[0]['mode']=='live':
            from ..providers.registry import complete
            limit=self.spec.get('limits',{}).get('max_model_calls',0)
            if self.calls>=limit: raise ReplayMismatch('Model call budget exceeded')
            self.calls+=1; event['replay_mode']='live'; rule=rules[0]
            body=copy.deepcopy(request); body['model']=rule['model']; body.update(rule.get('parameters',{}))
            event['request']=body;event['model']=body['model'];event['provider_kind']=self.project['providers'][rule['provider_id']].get('kind','openai_compatible')
            if 'max_tokens' in self.spec.get('limits',{}):
                bound=self.spec['limits']['max_tokens'];field='max_tokens' if 'max_tokens' in body else 'max_completion_tokens';body[field]=min(body.get(field,bound),bound)
            return complete(self.project,rule['provider_id'],body)
        source=self.matcher.take(event); event['source_step_id']=source['id'];event['replay_mode']='recorded'
        if source.get('status')=='error': self.raise_recorded(source.get('error') or {})
        if source.get('_output_replayability','complete')!='complete': raise ReplayMismatch('Historical model output is incomplete')
        if not isinstance(source.get('response'),dict): raise ReplayMismatch('Full historical model response missing')
        return copy.deepcopy(source['response'])
