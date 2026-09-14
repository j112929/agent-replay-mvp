"""Conservative boundary alignment; ambiguous groups remain visible."""
def key(s):
    return (s.get('boundary_id',s['name']),s['kind'],tuple(s.get('scope_path',[])),s.get('lane_id'),s.get('occurrence',1))

def align(a,b):
    pairs=[]; used=set(); added=[]; ambiguous=[]
    for right in b:
        exact=[left for left in a if left['id'] not in used and right.get('source_step_id')==left['id']]
        candidates=exact or [left for left in a if left['id'] not in used and key(left)==key(right)]
        if len(candidates)==1:
            left=candidates[0];used.add(left['id']);pairs.append((left,right,'exact' if exact or left.get('boundary_id') else 'inferred'))
        elif candidates:
            ambiguous.append({'candidate_id':right['id'],'source_ids':[s['id'] for s in candidates]})
        else: added.append(right['id'])
    return pairs,added,[s['id'] for s in a if s['id'] not in used],ambiguous
