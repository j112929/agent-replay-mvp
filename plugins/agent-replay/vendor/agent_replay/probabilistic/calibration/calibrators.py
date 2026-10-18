"""Small auditable logistic Newton fit and weighted PAVA, no ML dependency."""
import bisect
import math
from .schemas import probability
from ...serialization import digest

CLIP = 1e-12


def logit(p):
    p = min(1 - CLIP, max(CLIP, probability(p)))
    return math.log(p / (1 - p))


def sigmoid(x):
    return 1 / (1 + math.exp(-x)) if x >= 0 else math.exp(x) / (1 + math.exp(x))


def logistic_fit(rows, ridge=1e-4):
    xs, ys = [logit(r['probability']) for r in rows], [int(r['outcome']) for r in rows]
    if len(set(ys)) != 2 or max(xs) - min(xs) < 1e-10:
        return None
    a, b = 1., 0.
    def objective(a, b):
        return sum(max(a*x+b, 0) - y*(a*x+b) + math.log1p(math.exp(-abs(a*x+b))) for x, y in zip(xs, ys)) + ridge*(a*a+b*b)/2
    for _ in range(100):
        ps = [sigmoid(a*x+b) for x in xs]
        ga = sum((p-y)*x for p,y,x in zip(ps,ys,xs)) + ridge*a
        gb = sum(p-y for p,y in zip(ps,ys)) + ridge*b
        haa = sum(p*(1-p)*x*x for p,x in zip(ps,xs)) + ridge
        hab = sum(p*(1-p)*x for p,x in zip(ps,xs))
        hbb = sum(p*(1-p) for p in ps) + ridge
        det = haa*hbb - hab*hab
        if det < 1e-15:
            return None
        da, db = (hbb*ga-hab*gb)/det, (haa*gb-hab*ga)/det
        step, loss = 1., objective(a,b)
        while step > 1e-8 and objective(a-step*da,b-step*db) > loss:
            step /= 2
        a, b = a-step*da, b-step*db
        if max(abs(a), abs(b)) > 50:
            return None  # separation / unstable calibration slope
        if max(abs(step*da),abs(step*db)) < 1e-8:
            return {'slope': a, 'intercept': b}
    return None


def fit(rows, method='none', min_groups=20):
    if method not in ('none', 'platt', 'isotonic') or not rows:
        raise ValueError('Unsupported calibrator or empty fitting set')
    if any(r.get('split') != 'train' or type(r.get('outcome')) is not bool for r in rows):
        raise ValueError('Calibrator fitting requires labeled training data only')
    for row in rows:
        probability(row['probability'])
    groups = sorted({r['task_group'] for r in rows})
    if len(groups) < min_groups or len({r['outcome'] for r in rows}) != 2:
        raise ValueError('Insufficient fitting groups or classes')
    if len({(r['task_group'], r['trial_seed']) for r in rows}) != len(rows):
        raise ValueError('Duplicate fitting trial')
    model = {'schema_version': 'research-calibrator.v1', 'method': method, 'training_groups': groups,
             'training_fingerprints': sorted({r['provenance']['scenario_fingerprint'] for r in rows}),
             'training_digest': digest(rows), 'status': 'fitted_unvalidated', 'version': '', 'parameters': {}}
    if method == 'platt':
        parameters = logistic_fit(rows)
        if parameters is None:
            raise ValueError('Logistic calibration fit is ill-conditioned')
        model['parameters'] = parameters
    if method == 'isotonic':
        blocks = []
        for p in sorted({r['probability'] for r in rows}):
            labels = [int(r['outcome']) for r in rows if r['probability'] == p]
            blocks.append({'lo': p, 'hi': p, 'sum': sum(labels), 'n': len(labels)})
            while len(blocks) > 1 and blocks[-2]['sum']/blocks[-2]['n'] > blocks[-1]['sum']/blocks[-1]['n']:
                right, left = blocks.pop(), blocks.pop()
                blocks.append({'lo': left['lo'], 'hi': right['hi'], 'sum': left['sum']+right['sum'], 'n': left['n']+right['n']})
        # Retain both block boundaries: interpolation inside a pooled block must
        # remain constant, otherwise it no longer minimizes isotonic squared loss.
        xs,ys=[],[]
        for block in blocks:
            for x in sorted({block['lo'],block['hi']}):
                xs.append(x);ys.append(block['sum']/block['n'])
        model['parameters'] = {'x':xs,'y':ys}
    model['version'] = digest(model)[:16]
    return model


def transform(model, rows):
    if model.get('schema_version') != 'research-calibrator.v1':
        raise ValueError('Unknown calibrator schema')
    if model.get('version')!=digest({**model,'version':''})[:16]:
        raise ValueError('Calibrator content/version mismatch')
    if any(r.get('split') not in ('validation', 'test', 'shift') for r in rows):
        raise ValueError('Transform evaluation must be explicitly held out')
    if set(model['training_groups']) & {r['task_group'] for r in rows} or set(model['training_fingerprints']) & {r['provenance']['scenario_fingerprint'] for r in rows}:
        raise ValueError('Training/evaluation task or provenance leakage')
    result = []
    for row in rows:
        p = probability(row['probability'])
        params = model['parameters']
        if model['method'] == 'platt':
            p = sigmoid(params['slope']*logit(p)+params['intercept'])
        elif model['method'] == 'isotonic':
            xs, ys = params['x'], params['y']
            i = bisect.bisect_left(xs, p)
            p = ys[0] if i == 0 else ys[-1] if i == len(xs) else ys[i-1]+(ys[i]-ys[i-1])*(p-xs[i-1])/(xs[i]-xs[i-1])
        elif model['method'] != 'none':
            raise ValueError('Unknown calibrator method')
        result.append({**row, 'probability': p, 'calibrator_version': model['version'], 'calibrator_method': model['method']})
    return result
