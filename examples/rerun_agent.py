"""Capture a failing agent, then rerun the entrypoint with a tool fixture."""
from agent_replay import capture, replay_agent
from capture_demo import agent

try:
    with capture("Refund agent rerun example") as original:
        agent(original)
except RuntimeError:
    pass

experiment = replay_agent(original.trace, agent, tool_overrides={"payments.get_transaction": {"refundable": True, "transaction_id": "txn_demo"}})
print("Original:", original.trace["status"], original.path)
print("Rerun:", experiment.trace["status"], experiment.path)
print("Output:", experiment.trace.get("output"))
print("This example validates only its explicit refund_ready branch, not a real payment.")
