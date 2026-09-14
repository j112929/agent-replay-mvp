"""Match boundaries by causal scope, never by completion timing."""
from ..serialization import digest
from ..replay import ReplayMismatch

def matches(selector,event):
    for key,value in selector.items():
        actual = digest(event['input']) if key=='input_digest' else event.get(key)
        if actual!=value: return False
    return True

class Matcher:
    def __init__(self,steps): self.steps=steps; self.used=set()
    def take(self,event):
        candidates=[]
        for s in self.steps:
            if s['id'] in self.used or s['kind']!=event['kind'] or s['name']!=event['name']: continue
            if s.get('boundary_id') and s['boundary_id']!=event.get('boundary_id'): continue
            if s.get('scope_path',[])!=event.get('scope_path',[]): continue
            if s.get('lane_id')!=event.get('lane_id'): continue
            if s.get('boundary_id') and s.get('occurrence',1)!=event.get('occurrence',1): continue
            if s.get('input')==event.get('input'): candidates.append(s)
        if len(candidates)!=1: raise ReplayMismatch('Ambiguous recording' if candidates else 'No recording matches this boundary and input')
        s=candidates[0]
        if s.get('_input_replayability','complete')!='complete': raise ReplayMismatch('Recorded input is not recoverable')
        if '[REDACTED]' in str(s.get('input')) or '[MAX_DEPTH]' in str(s.get('input')): raise ReplayMismatch('Recorded input is incomplete')
        if s.get('status')=='running': raise ReplayMismatch('Recorded boundary did not finish')
        self.used.add(s['id']); return s
