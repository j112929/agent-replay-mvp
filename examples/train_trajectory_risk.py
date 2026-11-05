"""Build and train the trajectory-risk model from replay/regression artifacts."""
import argparse, json
from agent_replay.trajectory_dataset import build_from_directory, write_jsonl
from agent_replay.trajectory_training import train, save_model

def main():
    p=argparse.ArgumentParser()
    p.add_argument("artifact_root")
    p.add_argument("--dataset",default=".agent-replay/trajectory-risk.jsonl")
    p.add_argument("--model",default=".agent-replay/trajectory-risk-model.json")
    args=p.parse_args()
    samples=build_from_directory(args.artifact_root)
    write_jsonl(samples,args.dataset)
    model=train(samples);save_model(model,args.model)
    print(json.dumps({"samples":len(samples),"model":args.model,"metrics":model["metrics"]},indent=2))
if __name__=="__main__":main()
