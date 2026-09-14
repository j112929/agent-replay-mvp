from ..runtime.matcher import matches
from ..serialization import MISSING

OPS={'equals','not_equals','exists','lte','gte','contains','count','forbidden','execution_completed','no_unhandled_error'}

def pointer(value,path):
    if path=='':return value
    if not isinstance(path,str) or not path.startswith('/'):raise ValueError('Use a JSON Pointer')
    try:
        for bit in path[1:].split('/'):
            bit=bit.replace('~1','/').replace('~0','~')
            value=value[int(bit)] if isinstance(value,list) else value[bit]
        return value
    except (KeyError,IndexError,TypeError,ValueError):return MISSING

def evaluate(trace,assertions):
    if not assertions:raise ValueError('At least one business assertion is required')
    results=[]
    for rule in assertions:
        op=rule.get('op')
        if op not in OPS:raise ValueError('Unsupported assertion operator')
        selection=rule.get('select');steps=[s for s in trace['steps'] if matches(selection,s)] if selection else []
        expected=rule.get('expected');evidence=[s['id'] for s in steps]
        status=trace['execution']['status']
        if op=='execution_completed':actual=status;ok=status=='completed'
        elif op=='no_unhandled_error':actual=trace['execution'].get('error');ok=not actual and status=='completed'
        elif op=='count':actual=len(steps);ok=type(expected) is int and actual==expected
        elif op=='forbidden':actual=len(steps);ok=actual==0 and status=='completed'
        else:
            values=[pointer(s,rule.get('path','')) for s in steps] if selection else [pointer(trace,rule.get('path',''))]
            def check(v):
                if v is MISSING:return False
                if op=='exists':return True
                if op=='equals':return type(v)==type(expected) and v==expected
                if op=='not_equals':return type(v)!=type(expected) or v!=expected
                if op in ('lte','gte'):
                    return type(v) in (int,float) and type(expected) in (int,float) and (v<=expected if op=='lte' else v>=expected)
                if op=='contains':return isinstance(v,(str,list,dict)) and expected in v
                return False
            flags=[check(v) for v in values];quantifier=rule.get('quantifier','all')
            if quantifier not in ('all','any'):raise ValueError('Use quantifier all or any')
            ok=bool(flags) and (all(flags) if quantifier=='all' else any(flags));actual=[None if v is MISSING else v for v in values]
        results.append({'id':rule.get('id',op),'verdict':'pass' if ok else 'fail','expected':expected,'actual':actual,'evidence':evidence})
    return results
