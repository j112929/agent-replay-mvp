import json
import tempfile
import unittest
from pathlib import Path
from agent_replay.trajectory_dataset import samples_from_trace
from agent_replay.trajectory_training import train
from agent_replay.trajectory_model import predict

def step(i,status="completed"):
    return {"id":str(i),"name":"tool" if i<3 else f"s{i}","kind":"tool",
            "execution":{"status":status,"duration_ms":100},
            "input":{"present":True,"value":{},"replayability":"complete"},
            "output":{"present":True,"value":{},"replayability":"complete"}}

def trace(tid,failed=False,n=8):
    steps=[step(i,"error" if failed and i==3 else "completed") for i in range(n)]
    return {"schema_version":"2.0","id":tid,"name":"x","started_at":"2026-10-02T00:00:00Z",
            "execution":{"status":"error" if failed else "completed","duration_ms":n*100},
            "input":{"present":True,"value":{},"replayability":"complete"},
            "output":{"present":True,"value":{},"replayability":"complete"},"steps":steps,
            "failures":[{"type":"x"}] if failed else []}

class TrainingTest(unittest.TestCase):
    def test_prefixes_do_not_leak_terminal_failures(self):
        rows=samples_from_trace(trace("a",True),0)
        partial=[r for r in rows if r["progress"]<1]
        self.assertTrue(partial)
        self.assertTrue(all(r["features"]["failure_markers"]==0 for r in partial))

    def test_split_is_grouped_by_trace(self):
        rows=samples_from_trace(trace("same"),1)
        self.assertEqual(len({r["split"] for r in rows}),1)

    def test_training_and_inference_contract(self):
        rows=[]
        # Enough deterministic IDs to populate train/cal/test; training only requires train.
        for i in range(80):
            rows += samples_from_trace(trace(f"ok-{i}",False),1)
            rows += samples_from_trace(trace(f"bad-{i}",True),0)
        model=train(rows)
        self.assertEqual(model["schema_version"],"trajectory-risk-model.v1")
        good=predict(trace("new-good",False),model)
        bad=predict(trace("new-bad",True),model)
        self.assertGreater(good.success_probability,bad.success_probability)
        self.assertGreater(model["metrics"]["test"]["n"],0)

if __name__=="__main__": unittest.main()
