"""Evaluation for calibrated trajectory-risk models (stdlib only)."""
from __future__ import annotations
import math
from collections import defaultdict
from .trajectory_model import FEATURE_NAMES

def sigmoid(x):
    return 1/(1+math.exp(-max(-40,min(40,x))))

def score(row,model):
    z=[(float(row["features"].get(k,0))-model["means"][i])/model["scales"][i] for i,k in enumerate(model["feature_names"])]
    raw=model["bias"]+sum(w*x for w,x in zip(model["weights"],z))
    c=model.get("calibration",{});return sigmoid(c.get("a",1)*raw+c.get("b",0))

def roc_auc(y,p):
    pos=[x for x,t in zip(p,y) if t==1];neg=[x for x,t in zip(p,y) if t==0]
    if not pos or not neg:return None
    wins=sum(1 if a>b else .5 if a==b else 0 for a in pos for b in neg)
    return wins/(len(pos)*len(neg))

def pr_auc(y,p):
    if not any(y) or all(y):return None
    pairs=sorted(zip(p,y),reverse=True);tp=fp=0;prev_r=0.;area=0.;total=sum(y)
    for _,label in pairs:
        if label:tp+=1
        else:fp+=1
        r=tp/total;precision=tp/(tp+fp);area+=(r-prev_r)*precision;prev_r=r
    return area

def reliability(y,p,bins=10):
    out=[]
    for i in range(bins):
        lo=i/bins;hi=(i+1)/bins
        vals=[(yy,pp) for yy,pp in zip(y,p) if lo<=pp<(hi if i<bins-1 else hi+1e-12)]
        if vals:out.append({"lo":lo,"hi":hi,"n":len(vals),"mean_predicted":sum(v[1] for v in vals)/len(vals),"observed_success":sum(v[0] for v in vals)/len(vals)})
    return out

def evaluate(rows,model,threshold=.5):
    test=[r for r in rows if r["split"]=="test"];y=[r["label"] for r in test];p=[score(r,model) for r in test]
    brier=sum((a-b)**2 for a,b in zip(p,y))/len(y) if y else None
    progress=defaultdict(list)
    for r,prob in zip(test,p):progress[round(r["progress"],2)].append((r["label"],prob))
    curve=[{"progress":k,"n":len(v),"success_mean":sum(p for y,p in v if y==1)/max(1,sum(y==1 for y,p in v)),"failure_mean":sum(p for y,p in v if y==0)/max(1,sum(y==0 for y,p in v))} for k,v in sorted(progress.items())]

    # Earliest prefix whose risk crosses threshold; lead is remaining steps.
    grouped=defaultdict(list)
    for r,prob in zip(test,p):grouped[r["trace_id"]].append((r,prob))
    leads=[]
    for items in grouped.values():
        items.sort(key=lambda x:x[0]["prefix_steps"])
        if items[0][0]["label"]!=0:continue
        hit=next((r for r,prob in items if prob<threshold),None)
        if hit:leads.append(hit["total_steps"]-hit["prefix_steps"])
    return {"n":len(test),"brier":brier,"roc_auc":roc_auc(y,p),"pr_auc":pr_auc(y,p),
            "reliability":reliability(y,p),"progress_curve":curve,
            "failure_early_warning":{"threshold":threshold,"detected":len(leads),"failed_traces":sum(items[0][0]["label"]==0 for items in grouped.values()),"mean_steps_before_end":sum(leads)/len(leads) if leads else None,"lead_steps":leads}}
