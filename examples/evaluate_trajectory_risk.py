"""Train and evaluate trajectory risk; optionally emit SVG plots."""
import argparse,json
from pathlib import Path
from agent_replay.trajectory_dataset import build_from_directory,write_jsonl
from agent_replay.trajectory_training import train,save_model
from agent_replay.trajectory_evaluation import evaluate

def svg_line(points,xkey,ykeys,title,path):
    W,H=720,420;m=55
    def X(x):return m+x*(W-2*m)
    def Y(y):return H-m-y*(H-2*m)
    body=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}"><text x="{m}" y="25" font-size="18">{title}</text>',
          f'<line x1="{m}" y1="{H-m}" x2="{W-m}" y2="{H-m}" stroke="black"/><line x1="{m}" y1="{m}" x2="{m}" y2="{H-m}" stroke="black"/>']
    for key in ykeys:
        vals=[(float(p[xkey]),float(p[key])) for p in points if p.get(key) is not None]
        if vals:body.append('<polyline fill="none" stroke="black" stroke-width="2" points="'+' '.join(f'{X(x):.1f},{Y(y):.1f}' for x,y in vals)+'"/>')
    body.append('</svg>');Path(path).write_text(''.join(body))

def main():
    p=argparse.ArgumentParser();p.add_argument("artifact_root");p.add_argument("--out",default=".agent-replay/risk-eval");a=p.parse_args()
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    rows=build_from_directory(a.artifact_root);write_jsonl(rows,out/"dataset.jsonl")
    model=train(rows);save_model(model,out/"model.json");report=evaluate(rows,model)
    (out/"metrics.json").write_text(json.dumps(report,indent=2)+"\n")
    rel=[{"x":r["mean_predicted"],"observed_success":r["observed_success"]} for r in report["reliability"]]
    svg_line(rel,"x",["observed_success"],"Reliability diagram",out/"reliability.svg")
    svg_line(report["progress_curve"],"progress",["success_mean","failure_mean"],"P(success) vs trajectory progress",out/"progress.svg")
    print(json.dumps(report,indent=2))
if __name__=="__main__":main()
