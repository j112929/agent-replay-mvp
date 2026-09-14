from .matcher import matches
from ..replay import ReplayMismatch

class Fixtures:
    def __init__(self,items):
        self.items=items; self.used={}
        ids=[x.get('id') for x in items]
        if len(ids)!=len(set(ids)): raise ValueError('Duplicate fixture IDs')
        for x in items:
            if x.get('consume') not in ('once','sequence','repeat') or not x.get('selector'): raise ValueError('Fixture requires selector and explicit consumption')
    def resolve(self,event):
        choices=[x for x in self.items if matches(x['selector'],event)]
        if not choices: return None
        if len(choices)>1: raise ReplayMismatch('Multiple fixtures match boundary')
        item=choices[0]; n=self.used.get(item['id'],0)
        responses=item.get('responses',[]) if item['consume']=='sequence' else [item['response']]
        if item['consume']!='repeat' and n>=len(responses): raise ReplayMismatch('Fixture exhausted')
        self.used[item['id']]=n+1
        return responses[n if item['consume']=='sequence' else 0]
    def verify(self):
        for x in self.items:
            if 'expected_calls' in x and self.used.get(x['id'],0)!=x['expected_calls']: raise ReplayMismatch('Fixture expected_calls mismatch: '+x['id'])
