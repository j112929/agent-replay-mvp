from ..runtime.matcher import matches
from ..serialization import MISSING

def exact_equal(actual,expected):
    from ..serialization import canonical
    return canonical(actual)==canonical(expected)

def contains_value(actual,expected):
    if isinstance(actual,list):return any(exact_equal(item,expected) for item in actual)
    if isinstance(actual,(str,dict)):return isinstance(expected,str) and expected in actual
    return False

OPS={'equals','not_equals','exists','lte','gte','contains','count','forbidden','execution_completed','no_unhandled_error','json_schema','predicate','ordered'}

def validate_rules(assertions):
    from .predicates import validate_schema,validate_predicate
    from ..serialization import canonical
    canonical(assertions)
    if not isinstance(assertions,list) or not 1<=len(assertions)<=50:raise ValueError('Provide 1–50 declarative assertions')
    ids=set()
    selector_keys={'id','name','kind','boundary_id','scope_path','lane_id','occurrence','input_digest'}
    def selector(value):
        if not isinstance(value,dict) or not value or set(value)-selector_keys:raise ValueError('Invalid assertion selector')
        for key,item in value.items():
            if key=='scope_path':
                if not isinstance(item,list) or not all(isinstance(x,str) for x in item):raise ValueError('Invalid assertion scope')
            elif key=='occurrence':
                if type(item) is not int or item<1:raise ValueError('Invalid assertion occurrence')
            elif key=='lane_id' and item is None:continue
            elif not isinstance(item,str) or not item:raise ValueError('Invalid assertion selector value')
        if 'kind' in value and value['kind'] not in ('agent','llm','tool'):raise ValueError('Invalid assertion kind')
    for rule in assertions:
        if not isinstance(rule,dict) or set(rule)-{'id','op','select','path','expected','quantifier'}:raise ValueError('Invalid assertion fields')
        if not isinstance(rule.get('id'),str) or not 1<=len(rule['id'])<=80 or rule['id'] in ids:raise ValueError('Invalid assertion ID')
        ids.add(rule['id']);op=rule.get('op')
        if op not in OPS:raise ValueError('Unsupported assertion operator')
        if 'select' in rule:selector(rule['select'])
        if 'path' in rule and (not isinstance(rule['path'],str) or rule['path'] and not rule['path'].startswith('/')):raise ValueError('Use a JSON Pointer')
        if rule.get('quantifier','all') not in ('all','any'):raise ValueError('Invalid assertion quantifier')
        if op not in ('exists','forbidden','execution_completed','no_unhandled_error') and 'expected' not in rule:raise ValueError('Assertion expected value required')
        if op in ('count','forbidden') and not rule.get('select'):raise ValueError('Tool selection required')
        if op=='count' and (type(rule['expected']) is not int or rule['expected']<0):raise ValueError('Invalid count')
        if op in ('lte','gte') and type(rule['expected']) not in (int,float):raise ValueError('Numeric assertion required')
        if op=='json_schema':validate_schema(rule['expected'])
        if op=='predicate':validate_predicate(rule['expected'])
        if op=='ordered':
            if not isinstance(rule['expected'],list) or not 2<=len(rule['expected'])<=20:raise ValueError('Provide 2–20 ordered selectors')
            for item in rule['expected']:selector(item)
    return assertions

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
        elif op=='ordered':
            # Fail ambiguous/concurrent selections rather than use wall-clock order.
            groups=[[s for s in trace['steps'] if matches(sel,s)] for sel in expected]
            selected=[g[0] for g in groups if len(g)==1];evidence=[s['id'] for s in selected]
            positions=[trace['steps'].index(s) for s in selected];actual=evidence
            ok=len(selected)==len(expected) and len(set(evidence))==len(expected) and positions==sorted(positions) and len({s.get('lane_id') for s in selected})==1
            if ok:
                ok=all(a['execution'].get('start_ms',0)+a['execution']['duration_ms']<=b['execution'].get('start_ms',0) for a,b in zip(selected,selected[1:]))
        else:
            values=[pointer(s,rule.get('path','')) for s in steps] if selection else [pointer(trace,rule.get('path',''))]
            def check(v):
                if v is MISSING:return False
                if op=='exists':return True
                if op=='equals':return exact_equal(v,expected)
                if op=='not_equals':return not exact_equal(v,expected)
                if op in ('lte','gte'):
                    return type(v) in (int,float) and type(expected) in (int,float) and (v<=expected if op=='lte' else v>=expected)
                if op=='contains':return contains_value(v,expected)
                if op=='json_schema':
                    from .predicates import validate_schema,schema_matches
                    validate_schema(expected);return schema_matches(v,expected)
                if op=='predicate':
                    from .predicates import validate_predicate,predicate_matches
                    validate_predicate(expected);return predicate_matches(v,expected)
                return False
            flags=[check(v) for v in values];quantifier=rule.get('quantifier','all')
            if quantifier not in ('all','any'):raise ValueError('Use quantifier all or any')
            ok=bool(flags) and (all(flags) if quantifier=='all' else any(flags));actual=[None if v is MISSING else v for v in values]
        results.append({'id':rule.get('id',op),'verdict':'pass' if ok else 'fail','expected':expected,'actual':actual,'evidence':evidence})
    return results
