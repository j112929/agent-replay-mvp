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
            from ..security.replay import deny_execution
            deny_execution()
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
            from ..security.replay import deny_execution
            deny_execution()
        source=self.matcher.take(event); event['source_step_id']=source['id'];event['replay_mode']='recorded'
        if source.get('status')=='error': self.raise_recorded(source.get('error') or {})
        if source.get('_output_replayability','complete')!='complete': raise ReplayMismatch('Historical model output is incomplete')
        if not isinstance(source.get('response'),dict): raise ReplayMismatch('Full historical model response missing')
        return copy.deepcopy(source['response'])
