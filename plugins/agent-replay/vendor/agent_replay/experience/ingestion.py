"""Extract observed failures without rerunning code or modifying source traces."""
import re

from ..migrations import to_v2
from ..schema import validate_trace
from ..serialization import canonical, digest
from .contracts import validate

EXTRACTOR_VERSION = "failure-extractor.v1"
NORMALIZATION_VERSION = "failure-signature.v1"
INFRASTRUCTURE = {
    "ReplayMismatch": "replay_mismatch", "RecordedError": "replay_mismatch",
    "ProviderError": "provider_error",
}


def normalize_message(message):
    """Normalize volatile IDs; keep amounts, limits, status codes and operators."""
    text = message.casefold()
    text = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<uuid>", text)
    text = re.sub(r"\b[0-9a-f]{16,64}\b", "<hash>", text)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}t\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:z|[+-]\d{2}:\d{2})", "<time>", text)
    text = re.sub(r"\b((?:order|request|trace|run|user|session|job)[ _-]?id)\s*[=:]\s*[^\s,;]+",
                  r"\1=<id>", text)
    return " ".join(text.split())


def _eligibility(original, trace):
    reasons = []
    unknown = "capture_health" not in original
    if not isinstance(trace.get("capture_health", {}), dict):
        raise ValueError("Capture health must be an object")
    if unknown:
        reasons.append("capture_coverage_unknown")
    elif trace.get("capture_health", {}).get("state") != "complete":
        reasons.append("capture_incomplete")
    if trace["execution"]["status"] in ("running", "interrupted"):
        reasons.append("source_not_finished")
    if not trace["input"]["present"] or trace["input"]["replayability"] != "complete":
        reasons.append("run_input_unavailable")
    for step in trace["steps"]:
        if step["execution"]["status"] in ("running", "interrupted"):
            reasons.append("unfinished_boundary")
        if not step["input"]["present"] or step["input"]["replayability"] != "complete":
            reasons.append("boundary_input_unavailable")
        if step["output"]["replayability"] in ("redacted", "truncated", "unsupported") and (
                step["output"]["present"] or step["execution"]["status"] == "completed"):
            reasons.append("boundary_output_unavailable")
    reasons = sorted(set(reasons))
    blocked = any(reason != "capture_coverage_unknown" for reason in reasons)
    return {"state": "blocked" if blocked else "unknown" if unknown else "eligible", "reasons": reasons}


def _result_list(results):
    if results is None:
        return []
    if isinstance(results, dict):
        results = results.get("results", [results])
    if not isinstance(results, list) or not all(isinstance(x, dict) for x in results):
        raise ValueError("Regression results must be an object, suite, or list")
    return results


def validate_result(result):
    canonical(result)
    if result.get("schema_version") != "1.0" or result.get("verdict") not in (
            "pass", "fail", "error", "inconclusive", "not_evaluated"):
        raise ValueError("Invalid regression result version or verdict")
    assertions = result.get("assertions", [])
    if not isinstance(assertions, list) or not all(isinstance(x, dict) and x.get("verdict") in
            ("pass", "fail") and isinstance(x.get("id"), str) and x["id"] for x in assertions):
        raise ValueError("Invalid regression assertion evidence")
    if result.get("verdict") in ("pass", "fail") and not result.get("candidate_trace_id"):
        raise ValueError("Evaluated regression result requires candidate_trace_id")
    if result.get("verdict") in ("pass", "fail") and not assertions:
        raise ValueError("Evaluated regression result requires assertion evidence")
    if result.get("verdict") == "pass" and any(a["verdict"] == "fail" for a in assertions):
        raise ValueError("Passing regression result contains failed assertion evidence")
    return result


def extract_failures(trace, regression_results=None, *, source_file_digest=None):
    """Return one record per observed event; eligibility describes capture only.

    Regression assertions belong to their candidate, never to the historical
    source. Source-only error results may be associated by the ingest pipeline.
    """
    original = validate_trace(trace)
    normalized = to_v2(original)
    validate_trace(normalized)
    source = {"trace_id": original["id"], "trace_digest": digest(original),
              "normalized_digest": digest(normalized)}
    metadata = normalized.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("Trace metadata must be an object")
    family = metadata.get("task_family_id", normalized["name"])
    if not isinstance(family, str) or not family.strip():
        raise ValueError("task_family_id must be a non-empty string")
    steps = {step["id"]: step for step in normalized["steps"]}
    tool_sequence = [step.get("boundary_id") or step["name"] for step in normalized["steps"]
                     if step["kind"] == "tool"]
    eligibility = _eligibility(original, normalized)
    records = []

    def add(origin, category, domain, message, pointer, step_ids=(), error_type="", assertion_id="",
            artifact_digest=None, blocked=False):
        if not isinstance(message, str):
            message = canonical(message)
        if not isinstance(error_type, str):
            raise ValueError("Error type must be a string")
        if not isinstance(step_ids, (list, tuple)) or any(not isinstance(x, str) or x not in steps for x in step_ids):
            raise ValueError("Failure evidence references an unknown step")
        ids = sorted(set(step_ids))
        boundaries = sorted({steps[x].get("boundary_id") or steps[x]["name"] for x in ids})
        evidence = [{"artifact_digest": artifact_digest or source["trace_digest"], "pointer": pointer}]
        signature = {"normalization_version": NORMALIZATION_VERSION, "family_id": family,
                     "domain": domain, "category": category, "error_type": error_type,
                     "assertion_id": assertion_id, "boundary_ids": boundaries,
                     "tool_sequence": tool_sequence, "message": normalize_message(message)}
        available = eligibility
        if blocked:
            available = {"state": "blocked", "reasons": sorted(set(eligibility["reasons"] + [category]))}
        record = {"schema_version": "experience.failure.v1",
                  "id": digest({"source": source["trace_digest"], "origin": origin, "evidence": evidence}),
                  "source": source, "family_id": family, "origin": origin, "category": category,
                  "domain": domain, "message": message, "error_type": error_type,
                  "assertion_id": assertion_id, "step_ids": ids, "boundary_ids": boundaries,
                  "tool_sequence": tool_sequence, "eligibility": available, "evidence": evidence,
                  "signature": signature, "signature_digest": digest(signature)}
        records.append(validate(record))

    def error_category(error, kind):
        error_type = error.get("type", "")
        if error_type in INFRASTRUCTURE:
            return INFRASTRUCTURE[error_type], "infrastructure"
        return {"tool": "tool_error", "llm": "model_error", "agent": "agent_error"}[kind], "application"

    failures = normalized.get("failures", [])
    if not isinstance(failures, list) or not all(isinstance(x, dict) for x in failures):
        raise ValueError("Captured failures must be objects")
    for i, failure in enumerate(failures):
        category = failure.get("category", "manual")
        if not isinstance(category, str) or not category:
            raise ValueError("Invalid captured failure category")
        domain = "application"
        if category in ("assertion", "business_assertion"):
            category = "business_assertion"
        elif category in ("provider_error", "replay_mismatch", "capture_incomplete", "interrupted", "worker_error"):
            domain = "infrastructure"
        else:
            category = "manual:" + category
            domain = "unknown"
        step_ids = [failure["step_id"]] if failure.get("step_id") else []
        add("captured_failure", category, domain, failure.get("message", ""), f"/failures/{i}", step_ids)

    observed_errors = set()
    observed_step_error = False
    for i, step in enumerate(normalized["steps"]):
        error = step.get("error") or step["execution"].get("error")
        if error:
            observed_step_error = True
            if not isinstance(error, dict):
                raise ValueError("Step error must be an object")
            category, domain = error_category(error, step["kind"])
            location = "error" if step.get("error") else "execution/error"
            add("step_error", category, domain, error.get("message", ""), f"/steps/{i}/{location}",
                [step["id"]], error.get("type", ""), artifact_digest=source["normalized_digest"])
            observed_errors.add((error.get("type", ""), canonical(error.get("message", ""))))
        elif step["execution"]["status"] == "error":
            observed_step_error = True
            add("step_error", "unclassified_error", "unknown", "Boundary reported error without details",
                f"/steps/{i}/execution", [step["id"]], artifact_digest=source["normalized_digest"])
    error = normalized["execution"].get("error")
    if error:
        if not isinstance(error, dict):
            raise ValueError("Execution error must be an object")
        if (error.get("type", ""), canonical(error.get("message", ""))) not in observed_errors:
            category, domain = error_category(error, "agent")
            add("execution_error", category, domain, error.get("message", ""), "/execution/error",
                error_type=error.get("type", ""), artifact_digest=source["normalized_digest"])
    elif normalized["execution"]["status"] == "error" and not observed_step_error:
        add("execution_error", "unclassified_error", "unknown", "Execution reported error without details",
            "/execution", artifact_digest=source["normalized_digest"])
    if normalized["execution"]["status"] in ("running", "interrupted"):
        add("execution_error", "interrupted", "infrastructure", "Source execution did not finish",
            "/execution", artifact_digest=source["normalized_digest"], blocked=True)
    health = normalized.get("capture_health", {})
    if "capture_health" in original and health.get("state") != "complete":
        add("capture_gap", "capture_incomplete", "infrastructure", "Capture is incomplete",
            "/capture_health", artifact_digest=source["normalized_digest"], blocked=True)

    for result in sorted(_result_list(regression_results), key=digest):
        validate_result(result)
        if result.get("candidate_trace_id") and result["candidate_trace_id"] != original["id"]:
            raise ValueError("Regression result belongs to a different candidate trace")
        if not result.get("candidate_trace_id") and result.get("verdict") in ("error", "inconclusive"):
            if result.get("source_digest") not in {source["trace_digest"], source_file_digest} or not result.get("source_digest"):
                raise ValueError("Regression error result belongs to a different source trace")
        result_digest = digest(result)
        if result["verdict"] in ("error", "inconclusive"):
            category = "worker_error" if result["verdict"] == "error" else "replay_mismatch"
            add("regression_result", category, "infrastructure", result.get("reason", "Evidence unavailable"),
                "", artifact_digest=result_digest, blocked=True)
        elif result["verdict"] == "fail":
            for i, assertion in enumerate(result.get("assertions", [])):
                if assertion["verdict"] != "fail":
                    continue
                add("regression_assertion", "business_assertion", "application",
                    "Assertion failed: " + assertion["id"], f"/assertions/{i}", assertion.get("evidence", []),
                    assertion_id=assertion["id"], artifact_digest=result_digest)
    return sorted({record["id"]: record for record in records}.values(), key=lambda x: x["id"])
