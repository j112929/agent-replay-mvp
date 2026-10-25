"""Local evidence ingestion, clustering and deterministic refund mutation stages."""
from collections import defaultdict

from ..migrations import to_v2
from ..schema import validate_trace
from ..serialization import digest
from .clustering import ALGORITHM_VERSION, cluster_failures, resolve_config
from .contracts import validate
from .ingestion import EXTRACTOR_VERSION, _result_list, extract_failures, validate_result
from .stages import Stages


def ingest(traces, regression_results=None, *, directory=".replay/experience", seed=0, source_file_digests=None):
    if not isinstance(traces, list) or not traces:
        raise ValueError("Supply at least one trace")
    stages = Stages(directory)
    results = _result_list(regression_results)
    raw = {stages.objects.put(trace): trace for trace in traces}
    result_objects = {stages.objects.put(result): result for result in results}
    file_digests = source_file_digests or {}
    if not isinstance(file_digests, dict) or any(key not in raw or not isinstance(values, list) or
            any(not isinstance(v, str) or len(v) != 64 or any(c not in "0123456789abcdef" for c in v) for v in values)
            for key, values in file_digests.items()):
        raise ValueError("Invalid source file digests")
    config = {"trace_digests": sorted(raw), "regression_result_digests": sorted(result_objects),
              "source_file_digests": {key: sorted(set(values)) for key, values in sorted(file_digests.items())}}
    inputs = sorted(set(raw) | set(result_objects))

    def produce():
        records, rejections = {}, []
        valid_sources = {}
        by_id, by_source = defaultdict(list), defaultdict(list)
        for key in sorted(raw):
            trace = raw[key]
            try:
                validate_trace(trace)
                normalized = to_v2(trace)
                validate_trace(normalized)
                extracted = extract_failures(trace)
            except (ValueError, KeyError, TypeError) as exc:
                rejections.append({"artifact_digest": key, "reason": "Invalid trace: " + str(exc)})
                continue
            stages.objects.put(normalized)
            valid_sources[key] = trace
            by_id[trace["id"]].append(key)
            by_source[key].append(key)
            for checksum in file_digests.get(key, []):
                if key not in by_source[checksum]:
                    by_source[checksum].append(key)
            records.update({record["id"]: record for record in extracted})
        for key in sorted(result_objects):
            result = result_objects[key]
            try:
                validate_result(result)
                if result.get("candidate_trace_id"):
                    matches = by_id.get(result["candidate_trace_id"], [])
                else:
                    matches = by_source.get(result.get("source_digest"), [])
                if len(matches) != 1:
                    raise ValueError("Regression result has no unique supplied candidate/source trace")
                source_key = matches[0]
                extracted = extract_failures(valid_sources[source_key], result,
                                              source_file_digest=result.get("source_digest") if
                                              result.get("source_digest") in file_digests.get(source_key, []) else None)
                records.update({record["id"]: record for record in extracted})
            except (ValueError, KeyError, TypeError) as exc:
                rejections.append({"artifact_digest": key, "reason": "Invalid regression evidence: " + str(exc)})
        return {"schema_version": "experience.failures.v1", "extractor_version": EXTRACTOR_VERSION,
                "input_digests": inputs, "records": sorted(records.values(), key=lambda x: x["id"]),
                "rejections": sorted(rejections, key=lambda x: (x["artifact_digest"], x["reason"])),
                "trace_count": len(valid_sources), "result_count": len(result_objects)}

    return stages.run("ingest", EXTRACTOR_VERSION, inputs, config, seed, produce)


def cluster(dataset, *, directory=".replay/experience", config=None, overrides=None, seed=0):
    validate(dataset, "experience-failures")
    stages = Stages(directory)
    key = stages.objects.put(dataset)
    resolved = resolve_config(config)
    review = overrides or {"schema_version": "experience.overrides.v1", "groups": []}
    validate(review, "experience-overrides")
    # Canonicalize review ordering before deriving the stage key.
    review = {**review, "groups": sorted([{**group, "member_ids": sorted(group["member_ids"])}
                                         for group in review["groups"]], key=digest)}
    review_key = stages.objects.put(review)
    return stages.run("cluster", ALGORITHM_VERSION, [key, review_key], resolved, seed,
                      lambda: cluster_failures(dataset["records"], resolved, review))


def mutate(dataset, cluster_record, requirement, *, directory=".replay/experience", seed=0, budget=11, operators=None):
    """Resumable mutation consumes explicit policy requirements and parent evidence."""
    from .mutation import generate, validate_bundle, IMPLEMENTATION
    from .verifiers import implementation_digest
    stages = Stages(directory)
    inputs = [stages.objects.put(value) for value in (dataset, cluster_record, requirement)]
    outcome = stages.run("mutate", IMPLEMENTATION, inputs,
                         {"budget": budget, "operators": operators, "implementation_digest": implementation_digest()},
                         seed, lambda: generate(requirement, cluster_record, dataset, seed=seed, budget=budget, operators=operators))
    validate_bundle(outcome["value"])
    for entry in outcome["value"]["entries"]:
        for value in entry.values():
            stages.objects.put(value)
    return outcome
