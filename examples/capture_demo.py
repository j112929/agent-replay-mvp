"""A deterministic failure with no provider keys or network calls."""
from agent_replay import capture, tool

@tool
def lookup_order(order_id: str):
    return {"order_id": order_id, "item": "Studio headphones", "total": 129, "status": "delivered"}

@tool(name="payments.get_transaction")
def payment_lookup(order_id: str):
    raise RuntimeError("429 Too Many Requests: payment lookup failed; no refund was issued")

def agent(run):
    order = lookup_order(order_id="1042")
    with run.span("Determine refund eligibility", kind="agent", input=order) as decision:
        decision.set_output({"eligible": True, "amount": order["total"]})
    transaction = payment_lookup(order_id="1042")
    return {"refund_ready": bool(transaction.get("refundable")), "amount": order["total"], "note": "Example only; no refund is sent."}

if __name__ == "__main__":
    try:
        with capture("Refund a damaged order", metadata={"environment": "local-demo", "synthetic": True}) as run:
            agent(run)
    except RuntimeError as error:
        print(f"Expected demo failure: {error}")
    print(f"Captured: {run.path}")
    print("Run agent-replay serve, then open the local URL.")
