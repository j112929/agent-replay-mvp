import unittest

from agent_replay.trajectory_model import extract_features, predict


def trace(steps, failures=None):
    return {
        "schema_version": "2.0",
        "id": "t",
        "name": "demo",
        "started_at": "2026-10-02T00:00:00Z",
        "execution": {"status": "running", "duration_ms": 10},
        "input": {"present": True, "value": {}, "replayability": "complete"},
        "output": {"present": False, "replayability": "complete"},
        "steps": steps,
        "failures": failures or [],
    }


def step(i, kind="tool", status="completed", name=None):
    return {
        "id": str(i), "name": name or f"s{i}", "kind": kind,
        "execution": {"status": status, "duration_ms": 100},
        "input": {"present": True, "value": {}, "replayability": "complete"},
        "output": {"present": True, "value": {}, "replayability": "complete"},
    }


class TrajectoryModelTest(unittest.TestCase):
    def test_healthy_partial_trace_continues(self):
        value = predict(trace([step(i, kind="llm" if i % 2 else "tool") for i in range(6)]))
        self.assertGreater(value.success_probability, 0.75)
        self.assertEqual(value.recommended_action, "continue")
        self.assertEqual(value.failure_type, "none")

    def test_tool_errors_raise_risk(self):
        steps = [step(1), step(2, status="error"), step(3)]
        value = predict(trace(steps))
        self.assertLess(value.success_probability, 0.5)
        self.assertEqual(value.failure_type, "tool_execution")
        self.assertIn(value.recommended_action, ("replan", "escalate"))

    def test_loop_signal(self):
        steps = [step(i, name="search") for i in range(8)]
        features = extract_features(trace(steps))
        value = predict(trace(steps))
        self.assertGreater(features["repeat_rate"], 0.15)
        self.assertEqual(value.failure_type, "looping")


if __name__ == "__main__":
    unittest.main()
