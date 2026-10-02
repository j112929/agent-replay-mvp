"""Dependency-free logistic training + Platt calibration for trajectory risk."""
from __future__ import annotations
import json, math
from pathlib import Path
from .trajectory_model import FEATURE_NAMES

def sigmoid(x):
    if x >= 0:
        z=math.exp(-x); return 1/(1+z)
    z=math.exp(x); return z/(1+z)

def _xy(rows):
    return [[float(r["features"].get(k,0)) for k in FEATURE_NAMES] for r in rows], [int(r["label"]) for r in rows]

def _standardize(x):
    if not x: return [], [], []
    means=[sum(row[j] for row in x)/len(x) for j in range(len(x[0]))]
    scales=[]
    for j,m in enumerate(means):
        v=sum((row[j]-m)**2 for row in x)/len(x); scales.append(max(math.sqrt(v),1e-6))
    return [[(row[j]-means[j])/scales[j] for j in range(len(means))] for row in x],means,scales

def _fit(x,y,epochs=1200,lr=.05,l2=.01):
    w=[0.0]*len(x[0]); b=0.0
    for _ in range(epochs):
        gw=[0.0]*len(w); gb=0.0
        for row,label in zip(x,y):
            p=sigmoid(b+sum(a*z for a,z in zip(w,row))); d=p-label; gb+=d
            for j,z in enumerate(row): gw[j]+=d*z
        n=len(x); b-=lr*gb/n
        for j in range(len(w)): w[j]-=lr*(gw[j]/n+l2*w[j])
    return w,b

def _fit_platt(scores,y):
    # p = sigmoid(a*raw_logit + b), fitted only on calibration traces.
    x=[[s] for s in scores]; w,b=_fit(x,y,epochs=800,lr=.03,l2=.001); return w[0],b

def train(rows):
    train_rows=[r for r in rows if r["split"]=="train"]
    cal_rows=[r for r in rows if r["split"]=="calibration"]
    test_rows=[r for r in rows if r["split"]=="test"]
    if len(train_rows)<4 or len({r["label"] for r in train_rows})<2:
        raise ValueError("Need at least four training samples with both outcomes")
    x,y=_xy(train_rows); xs,means,scales=_standardize(x); w,b=_fit(xs,y)
    def raw(row):
        z=[(float(row["features"].get(k,0))-means[j])/scales[j] for j,k in enumerate(FEATURE_NAMES)]
        return b+sum(a*v for a,v in zip(w,z))
    if cal_rows and len({r["label"] for r in cal_rows})==2:
        a,cb=_fit_platt([raw(r) for r in cal_rows],[r["label"] for r in cal_rows])
    else: a,cb=1.0,0.0
    def metrics(part):
        if not part:return {"n":0}
        ps=[sigmoid(a*raw(r)+cb) for r in part]; ys=[r["label"] for r in part]
        brier=sum((p-y)**2 for p,y in zip(ps,ys))/len(ps)
        acc=sum((p>=.5)==bool(y) for p,y in zip(ps,ys))/len(ps)
        return {"n":len(part),"accuracy":round(acc,4),"brier":round(brier,4)}
    return {"schema_version":"trajectory-risk-model.v1","feature_names":list(FEATURE_NAMES),
            "means":means,"scales":scales,"weights":w,"bias":b,
            "calibration":{"method":"platt","a":a,"b":cb},
            "metrics":{"train":metrics(train_rows),"calibration":metrics(cal_rows),"test":metrics(test_rows)}}

def load_jsonl(path):
    with Path(path).open() as f:return [json.loads(line) for line in f if line.strip()]

def save_model(model,path):
    Path(path).write_text(json.dumps(model,indent=2,sort_keys=True)+"\n")
