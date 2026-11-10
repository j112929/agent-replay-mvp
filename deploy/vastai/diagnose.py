"""Summarize a retained rollout database without modifying it or revealing leases."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sqlite3


def diagnose(path):
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=5) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        counts = [dict(row) for row in db.execute(
            "SELECT kind,state,count(*) AS count FROM jobs GROUP BY kind,state ORDER BY kind,state")]
        versions = [row[0] for row in db.execute("SELECT version FROM policies ORDER BY version")]
        trajectories = db.execute(
            "SELECT count(*),sum(reward IS NOT NULL),sum(batch IS NOT NULL) FROM trajectories").fetchone()
        errors = Counter()
        for row in db.execute("SELECT kind,result FROM jobs WHERE result IS NOT NULL"):
            try:
                value = json.loads(row["result"])
            except (TypeError, ValueError):
                continue
            error = value.get("error") if isinstance(value, dict) else None
            if error:
                # Worker failures normally store only an exception class. Never print
                # arbitrary backend messages, payloads, tokens, owners, or settings.
                label = error if isinstance(error, str) and re.fullmatch(r"[A-Za-z_]\w{0,99}", error) else "message_omitted"
                errors[(row["kind"], label)] += 1
        recent = [dict(row) for row in db.execute(
            "SELECT kind,state,attempts,max_attempts FROM jobs ORDER BY created DESC LIMIT 20")]
    active = {row["kind"] for row in counts if row["state"] in ("queued", "leased")}
    stage = next((name for name in ("learner", "verifier", "actor") if name in active), "none_recorded")
    return {
        "schema_version": "gpu-smoke-diagnosis.v1",
        "policy_versions": versions,
        "committed_updates": max(versions) - min(versions) if versions else 0,
        "job_counts": counts,
        "trajectories": {"total": trajectories[0], "verified": trajectories[1] or 0,
                         "assigned_to_batch": trajectories[2] or 0},
        "last_unfinished_stage": stage,
        "retained_error_classes": [{"kind": kind, "error": error, "count": count}
                                   for (kind, error), count in sorted(errors.items())],
        "recent_job_states": recent,
        "note": "Retained state after stopping; this does not establish a root cause or a live process state.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    print(json.dumps(diagnose(args.database), indent=2))
