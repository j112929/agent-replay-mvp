"""Run the trajectory-risk MVP without external model/API dependencies."""
import json
from agent_replay.trajectory_model import predict

partial = {
    "schema_version": "2.0",
    "id": "run_123",
    "name": "research-agent",
    "started_at": "2026-10-02T00:00:00Z",
    "execution": {"status": "running", "duration_ms": 1900},
    "input": {"present": True, "value": {"goal": "research topic"}, "replayability": "complete"},
    "output": {"present": False, "replayability": "complete"},
    "steps": [
        {"id": "1", "name": "plan", "kind": "llm", "execution": {"status": "completed", "duration_ms": 500}},
        {"id": "2", "name": "search", "kind": "tool", "execution": {"status": "completed", "duration_ms": 400}},
        {"id": "3", "name": "search", "kind": "tool", "execution": {"status": "error", "duration_ms": 1000}},
    ],
}
print(json.dumps(predict(partial).to_dict(), indent=2))
