"""Domain-appropriate errors; categorical labels and numeric state stay separate."""
import math


def state_error(predicted, actual, fields=('progress','health'), scale=None):
    errors={}
    for key in fields:
        a,b=predicted.get(key),actual.get(key)
        if type(a) not in (float,int) or type(b) not in (float,int) or not math.isfinite(a) or not math.isfinite(b):
            continue
        errors[key]={'absolute_error':abs(a-b),'squared_error':(a-b)**2}
        if scale and key in scale:
            if scale[key]<=0: raise ValueError('Positive normalization scale required')
            errors[key]['normalized_absolute_error']=abs(a-b)/scale[key]
    return errors


def empirical_crps(samples, observed):
    if not samples or type(observed) not in (float,int) or not math.isfinite(observed) or any(type(x) not in (float,int) or not math.isfinite(x) for x in samples):
        raise ValueError('Numeric predictive samples and observation required')
    n=len(samples)
    return sum(abs(x-observed) for x in samples)/n-sum(abs(x-y) for x in samples for y in samples)/(2*n*n)
