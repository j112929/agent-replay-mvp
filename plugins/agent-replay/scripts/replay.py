#!/usr/bin/env python3
"""Codex-friendly inspection and comparison around the bundled replay SDK."""
import argparse
import json
from pathlib import Path
import sys

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "vendor"))
from agent_replay import capture, tool, replay_agent
from agent_replay.cli import load_trace, main as sdk_main
from agent_replay.schema import sanitize


def summarize(trace):
    return {"id": trace["id"], "name": trace["name"], "status": trace["status"],
            "duration_ms": trace["duration_ms"], "replay": trace.get("replay"),
            "steps": [{k: s.get(k) for k in ("id", "name", "kind", "status", "model", "duration_ms", "error")}
                      for s in trace["steps"]]}


def diff(before, after, path="$", limit=100):
    rows = []
    missing = object()
    def visit(a, b, location, depth=0):
        if len(rows) >= limit:
            return
        if a == b:
            return
        if depth < 25 and isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                visit(a.get(key, missing), b.get(key, missing), location + "." + key, depth + 1)
        elif depth < 25 and isinstance(a, list) and isinstance(b, list):
            for index in range(max(len(a), len(b))):
                visit(a[index] if index < len(a) else missing, b[index] if index < len(b) else missing,
                      location + f"[{index}]", depth + 1)
        else:
            rows.append({"path": location, "before_present": a is not missing,
                         "after_present": b is not missing,
                         "before": None if a is missing else a, "after": None if b is missing else b})
    visit(before, after, path)
    return rows


def compare(original, experiment, limit=100):
    provenance = experiment.get("replay") or {}
    if provenance.get("source_trace_id") != original["id"]:
        raise ValueError("Replay source_trace_id does not match the supplied original")
    if provenance.get("scope") == "step":
        source = next((s for s in original["steps"] if s["id"] == provenance.get("source_step_id")), None)
        if source is None or len(experiment["steps"]) != 1:
            raise ValueError("Single-step replay requires its matching source and one result step")
        fields = ("status", "output", "error", "model", "duration_ms")
        before = {k: source.get(k) for k in fields}
        after = {k: experiment["steps"][0].get(k) for k in fields}
    else:
        before, after = summarize(original), summarize(experiment)
        before["output"] = original.get("output")
        after["output"] = experiment.get("output")
    changes = diff(before, after, limit=limit)
    return {"source_trace_id": original["id"], "experiment_id": experiment["id"],
            "mode": provenance.get("mode"), "scope": provenance.get("scope"),
            "application_validated": provenance.get("application_validated", False),
            "changes": changes, "limit_reached": len(changes) >= limit,
            "note": "Recorded timing is historical; fixture application does not establish agent recovery."}


def demo(directory):
    @tool
    def payment_lookup(order_id):
        raise RuntimeError("Synthetic 429: payment lookup failed; no payment API was called")
    def agent(run):
        return {"refund_ready": payment_lookup(order_id="1042")["refundable"]}
    try:
        with capture("Plugin demo", directory=directory, metadata={"synthetic": True}) as original:
            agent(original)
    except RuntimeError:
        pass
    rerun = replay_agent(original.trace, agent, tool_overrides={"payment_lookup": {"refundable": True}}, directory=directory)
    return {"original": str(original.path.resolve()), "replay": str(rerun.path.resolve()),
            "original_status": original.trace["status"], "replay_status": rerun.trace["status"],
            "synthetic": True, "external_calls": 0}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("replay", "serve"):
        return sdk_main(argv)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Summarize a trajectory or inspect one boundary")
    inspect.add_argument("trace")
    inspect.add_argument("--step")
    compare_cmd = commands.add_parser("compare", help="Compare a replay against its actual source")
    compare_cmd.add_argument("original")
    compare_cmd.add_argument("experiment")
    compare_cmd.add_argument("--limit", type=int, default=100)
    example = commands.add_parser("demo", help="Capture and recover a synthetic failure; no network")
    example.add_argument("--directory", default=".replay/traces")
    commands.add_parser("replay", help="Use replay --help for recorded, fixture, model, or local-tool replay")
    commands.add_parser("serve", help="Use serve --help to open the local debugger")
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            trace = load_trace(args.trace)
            if args.step:
                output = next((s for s in trace["steps"] if s["id"] == args.step), None)
                if output is None:
                    raise ValueError("Step not found")
            else:
                output = summarize(trace)
        elif args.command == "compare":
            if not 1 <= args.limit <= 1000:
                raise ValueError("limit must be between 1 and 1000")
            output = compare(load_trace(args.original), load_trace(args.experiment), args.limit)
        else:
            output = demo(args.directory)
        print(json.dumps(sanitize(output), indent=2, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"error": sanitize(str(exc))}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
