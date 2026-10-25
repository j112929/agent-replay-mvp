"""Versioned sidecars; existing trajectory contracts remain compatible.

The dependency-free validator implements only the JSON Schema keywords used
below. The same definitions are published by scripts/generate_schemas.py.
"""
import re

from ..serialization import canonical, digest


def obj(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


def array(items, minimum=0, unique=False):
    value = {"type": "array", "items": items, "minItems": minimum}
    if unique:
        value["uniqueItems"] = True
    return value


TEXT = {"type": "string", "minLength": 1}
STRING = {"type": "string"}
SHA = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
UINT = {"type": "integer", "minimum": 0}
SEED = {"type": "integer"}
RATE = {"type": "number", "minimum": 0, "maximum": 1}
JSON_OBJECT = {"type": "object"}
IDS = array(TEXT, unique=True)
HASHES = array(SHA, unique=True)


def enum(*values):
    return {"enum": list(values)}


def contract(version, fields, required=None):
    properties = {"schema_version": {"const": version}, **fields}
    return obj(properties, None if required is None else ["schema_version", *required])


SOURCE = obj({"trace_id": TEXT, "trace_digest": SHA, "normalized_digest": SHA})
ELIGIBILITY = obj({"state": enum("eligible", "blocked", "unknown"), "reasons": IDS})
EVIDENCE = obj({"artifact_digest": SHA, "pointer": {"type": "string", "pattern": "^(/.*)?$"}})
SIGNATURE = obj({"normalization_version": {"const": "failure-signature.v1"},
                 "family_id": TEXT, "domain": enum("application", "infrastructure", "unknown"),
                 "category": TEXT, "error_type": STRING, "assertion_id": STRING,
                 "boundary_ids": IDS, "tool_sequence": array(TEXT), "message": STRING})
CONFIG = obj({"algorithm_version": {"const": "deterministic.v1"},
              "similarity_threshold": {"type": "number", "minimum": 0.5, "maximum": 1},
              "min_cluster_size": {"type": "integer", "minimum": 1, "maximum": 10000}})
REVIEW = obj({"reviewer": TEXT, "reason": TEXT})
OVERRIDE = obj({"member_ids": array(SHA, 1, unique=True), "label": TEXT, "review": REVIEW})

FAILURE = contract("experience.failure.v1", {
    "id": SHA, "source": SOURCE, "family_id": TEXT,
    "origin": enum("captured_failure", "step_error", "execution_error", "regression_assertion",
                   "regression_result", "capture_gap"),
    "category": TEXT, "domain": enum("application", "infrastructure", "unknown"),
    "message": STRING, "error_type": STRING, "assertion_id": STRING,
    "step_ids": IDS, "boundary_ids": IDS, "tool_sequence": array(TEXT),
    "eligibility": ELIGIBILITY, "evidence": array(EVIDENCE, 1),
    "signature": SIGNATURE, "signature_digest": SHA,
})
CLUSTER = contract("experience.cluster.v1", {
    "id": SHA, "label": TEXT, "domain": enum("application", "infrastructure", "unknown"),
    "family_id": TEXT, "member_ids": array(SHA, 1, unique=True),
    "representative_ids": array(SHA, 1, unique=True), "confidence": RATE,
    "method": enum("automatic", "reviewed"), "config_digest": SHA, "review": REVIEW,
}, ["id", "label", "domain", "family_id", "member_ids", "representative_ids",
    "confidence", "method", "config_digest"])
CLUSTER_SET = contract("experience.cluster-set.v1", {
    "records_digest": SHA, "config": CONFIG, "config_digest": SHA,
    "overrides": array(OVERRIDE), "clusters": array(CLUSTER),
    "outliers": array(obj({"failure_id": SHA, "reason": TEXT})),
})
REJECTION = obj({"artifact_digest": SHA, "reason": TEXT})
DATASET = contract("experience.failures.v1", {
    "extractor_version": {"const": "failure-extractor.v1"},
    "input_digests": HASHES, "records": array(FAILURE), "rejections": array(REJECTION),
    "trace_count": UINT, "result_count": UINT,
})
STAGE = contract("experience.stage.v1", {
    "key": SHA, "stage": enum("ingest", "cluster", "mutate"), "implementation_version": TEXT,
    "input_digests": HASHES, "config": JSON_OBJECT, "seed": SEED,
    "state": enum("running", "completed", "failed"), "attempt": {"type": "integer", "minimum": 1},
    "output_digest": SHA, "error": STRING,
}, ["key", "stage", "implementation_version", "input_digests", "config", "seed", "state", "attempt"])

# Data contracts do not select executable code; compilation uses a trusted registry.
SCENARIO = contract("experience.scenario.v1", {
    "id": SHA, "parent_failure_ids": array(SHA, 1, unique=True), "parent_cluster_id": SHA,
    "family_id": TEXT, "input": {},
    "mutation": obj({"operator_id": TEXT, "parameters": JSON_OBJECT}),
    "seed": SEED, "difficulty": TEXT, "environment_digest": SHA, "verifier_digest": SHA,
})
ENVIRONMENT = contract("experience.environment.v1", {
    "id": SHA, "adapter_id": TEXT, "initial_state": JSON_OBJECT,
    "fixture_digests": HASHES, "asset_digests": HASHES, "dependency_versions": JSON_OBJECT,
    "reset_adapter_id": TEXT,
    "limits": obj({"timeout_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 3600},
                   "max_steps": UINT, "max_model_calls": UINT}),
})
ASSERTION = obj({"id": TEXT, "op": enum("equals", "not_equals", "exists", "lte", "gte", "contains",
                                      "count", "forbidden", "execution_completed", "no_unhandled_error"),
                 "select": JSON_OBJECT, "path": STRING, "expected": {}, "quantifier": enum("all", "any")},
                ["id", "op"])
VERIFIER = contract("experience.verifier.v1", {
    "id": SHA, "evaluator_id": TEXT, "assertions": array(ASSERTION, 1),
    "requirement_digests": array(SHA, 1, unique=True),
    "qualification": obj({"state": enum("draft", "qualified", "rejected"), "evidence_digests": HASHES}),
})
SCENARIO_RESULT = contract("experience.scenario-result.v1", {
    "id": SHA, "scenario_digest": SHA, "environment_digest": SHA, "verifier_digest": SHA,
    "candidate": obj({"id": TEXT, "version": TEXT}), "seed": SEED,
    "trace_digest": SHA, "verdict": enum("pass", "fail", "inconclusive", "error"),
    "evidence": array(EVIDENCE), "fidelity": JSON_OBJECT,
    "duration_ms": {"type": "number", "minimum": 0}, "usage": JSON_OBJECT,
})
MANIFEST = contract("experience.dataset.v1", {
    "profile": enum("scenario", "evaluated-experience", "policy-rollout"),
    "schema_versions": IDS,
    "artifacts": array(obj({"path": TEXT, "sha256": SHA})),
    "splits": obj({"train": HASHES, "validation": HASHES, "test": HASHES}),
    "eligibility": ELIGIBILITY, "exclusions": array(REJECTION), "generation": JSON_OBJECT,
})
OVERRIDES = contract("experience.overrides.v1", {"groups": array(OVERRIDE)})
LABELS = contract("experience.labels.v1", {
    "review": REVIEW, "scope": enum("synthetic", "adjudicated"),
    "labels": array(obj({"failure_id": SHA, "mode": TEXT}), 2),
})
NULLABLE_RATE = {"anyOf": [RATE, {"type": "null"}]}
QUALITY = contract("experience.cluster-quality.v1", {
    "labels_digest": SHA, "review": REVIEW, "scope": enum("synthetic", "adjudicated"),
    "labeled_records": UINT, "unlabeled_records": UINT,
    "true_positive_pairs": UINT, "false_positive_pairs": UINT, "false_negative_pairs": UINT,
    "pairwise_precision": NULLABLE_RATE, "pairwise_recall": NULLABLE_RATE,
    "targets": obj({"pairwise_precision": RATE, "pairwise_recall": RATE}),
    "gate": enum("pass", "fail", "not_evaluated"), "uncertainty": TEXT,
})
REPORT = contract("experience.report.v1", {
    "dataset_digest": SHA, "cluster_set_digest": SHA,
    "summary": obj({"traces": UINT, "failures": UINT, "clusters": UINT, "outliers": UINT,
                    "rejections": UINT, "domains": JSON_OBJECT}),
    "dataset": DATASET, "cluster_set": CLUSTER_SET,
    "quality": {"anyOf": [QUALITY, {"type": "null"}]},
})
SCHEMAS = {
    "experience-failure": FAILURE, "experience-cluster": CLUSTER,
    "experience-cluster-set": CLUSTER_SET, "experience-failures": DATASET,
    "experience-stage": STAGE, "experience-scenario": SCENARIO,
    "experience-environment": ENVIRONMENT, "experience-verifier": VERIFIER,
    "experience-scenario-result": SCENARIO_RESULT, "experience-dataset": MANIFEST,
    "experience-overrides": OVERRIDES, "experience-labels": LABELS,
    "experience-cluster-quality": QUALITY, "experience-report": REPORT,
}


REFUND_REQUIREMENT = contract("experience.refund-requirement.v1", {
    "authority": TEXT, "order_id": TEXT, "currency": TEXT,
    "approved_minor": {"type": "integer", "minimum": 2, "maximum": 1000000000},
    "paid_minor": {"type": "integer", "minimum": 2, "maximum": 1000000000},
    "refunded_minor": UINT,
    "requested_minor": {"type": "integer", "minimum": 2, "maximum": 1000000000},
    "require_exactly_one": {"const": True},
})
SCENARIO_SET = contract("experience.scenario-set.v1", {
    "implementation_version": {"const": "refund-mutation.v1"}, "seed": SEED,
    "parent_cluster_digest": SHA, "failure_dataset_digest": SHA,
    "entries": array(obj({"scenario": SCENARIO, "environment": ENVIRONMENT,
                          "verifier": VERIFIER, "requirement": REFUND_REQUIREMENT}), 1),
})
QUALIFICATION = contract("experience.qualification.v1", {
    "scenario_digest": SHA, "environment_digest": SHA, "verifier_digest": SHA,
    "requirement_digest": SHA, "implementation_digest": SHA, "source_digest": SHA,
    "state": enum("qualified", "rejected"), "repeats": {"const": 100},
    "stable": {"type": "boolean"}, "witnesses": JSON_OBJECT,
    "evidence_digests": array(SHA, 1, unique=True),
})
SCHEMAS.update({"experience-refund-requirement": REFUND_REQUIREMENT,
                "experience-scenario-set": SCENARIO_SET, "experience-qualification": QUALIFICATION})


MEASUREMENT = obj({"value": NULLABLE_RATE, "numerator": UINT, "denominator": UINT,
                   "state": enum("measured", "unmeasured"), "scope": TEXT})
METRIC_NAMES = ("execution_rate", "decisive_verdict_rate", "success_rate", "qualification_rate",
               "mandatory_negative_rejection_rate", "identical_evidence_stability",
               "repeat_candidate_verdict_consistency", "cross_candidate_failure_reproduction", "new_failure_mode_rate",
               "cross_model_failure_reproduction", "held_out_task_success_improvement")
EVALUATION = contract("experience.evaluation.v1", {
    "implementation_version": {"const": "refund-evaluation.v1"}, "bundle_digest": SHA,
    "report_digest": SHA, "baseline_digest": {"anyOf": [SHA, {"type": "null"}]},
    "scenario_count": UINT, "result_count": UINT, "verdict_counts": JSON_OBJECT,
    "metrics": obj({name: MEASUREMENT for name in METRIC_NAMES}), "candidates": array(JSON_OBJECT),
    "operator_coverage": JSON_OBJECT, "observed_failure_modes": JSON_OBJECT,
    "exclusions": array(REJECTION), "measurement_notes": IDS,
})
EXPORT_RECORD = contract("experience.export-record.v1", {
    "id": SHA, "profile": enum("scenario", "evaluated-experience"),
    "scenario_digest": SHA, "environment_digest": SHA, "verifier_digest": SHA,
    "qualification_digest": SHA, "requirement_digest": SHA, "source_trace_digest": SHA,
    "parent_failure_ids": array(SHA, 1, unique=True), "parent_cluster_id": SHA,
    "family_id": TEXT, "input": {}, "seed": SEED, "split": enum("train", "validation", "test"),
    "group_id": SHA, "result_digest": {"anyOf": [SHA, {"type": "null"}]},
    "trace_digest": {"anyOf": [SHA, {"type": "null"}]},
    "candidate": {"anyOf": [obj({"id": TEXT, "version": TEXT}), {"type": "null"}]},
    "verdict": enum("pass", "fail", None), "reward": enum(0, 1, None),
})
SCHEMAS.update({"experience-evaluation": EVALUATION, "experience-export-record": EXPORT_RECORD})
COLLECTION_RECORD = contract("experience.collection-record.v1", {
    **{k: v for k, v in EXPORT_RECORD['properties'].items() if k != 'schema_version'},
    "child": SHA, "original_record_id": SHA,
})
COLLECTION = contract("refund-collection.v1", {
    "seed": SEED, "ratios": array(RATE, 3), "children": array(SHA, 3, unique=True),
    "splits": obj({name: array(SHA, 1, unique=True) for name in ('train','validation','test')}),
    "record_count": UINT, "split_group_count": UINT,
    "split_policy": {"const": "connected-source-family-parent-equivalent-task.v2"},
    "policy_rollout_ready": {"const": False},
    "artifacts": array(obj({"path": TEXT, "sha256": SHA}), 1),
})
SCHEMAS.update({"experience-collection-record": COLLECTION_RECORD, "experience-collection": COLLECTION})



def _validate(value, schema, path):
    if "anyOf" in schema:
        for choice in schema["anyOf"]:
            try:
                _validate(value, choice, path)
                return
            except ValueError:
                pass
        raise ValueError(path + ": no allowed shape matches")
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        raise ValueError(path + ": unexpected constant")
    if "enum" in schema and not any(type(value) is type(x) and value == x for x in schema["enum"]):
        raise ValueError(path + ": unexpected value")
    kind = schema.get("type")
    valid = {"object": isinstance(value, dict), "array": isinstance(value, list),
             "string": isinstance(value, str), "integer": type(value) is int,
             "number": type(value) in (int, float), "boolean": type(value) is bool,
             "null": value is None}
    if kind and not valid[kind]:
        raise ValueError(path + ": expected " + kind)
    if kind == "object":
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(path + ": missing " + key)
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                _validate(item, properties[key], path + "/" + key)
            elif schema.get("additionalProperties") is False:
                raise ValueError(path + ": unsupported field " + key)
    elif kind == "array":
        if len(value) < schema.get("minItems", 0):
            raise ValueError(path + ": too few items")
        if schema.get("uniqueItems") and len({canonical(x) for x in value}) != len(value):
            raise ValueError(path + ": duplicate items")
        for i, item in enumerate(value):
            _validate(item, schema.get("items", {}), path + "/" + str(i))
    elif kind == "string":
        if len(value) < schema.get("minLength", 0):
            raise ValueError(path + ": empty string")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            raise ValueError(path + ": invalid format")
    elif kind in ("number", "integer"):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(path + ": below minimum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise ValueError(path + ": below exclusive minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(path + ": above maximum")


def validate(value, name=None):
    """Validate a JSON sidecar, including cross-field integrity constraints."""
    canonical(value)
    if name is None:
        version = value.get("schema_version") if isinstance(value, dict) else None
        name = next((key for key, schema in SCHEMAS.items()
                     if schema["properties"]["schema_version"]["const"] == version), None)
    if name not in SCHEMAS:
        raise ValueError("Unknown experience contract")
    _validate(value, SCHEMAS[name], name)
    if name == "experience-evaluation":
        for m in value["metrics"].values():
            if m["numerator"] > m["denominator"]:
                raise ValueError("Metric numerator exceeds denominator")
            expected = m["numerator"] / m["denominator"] if m["denominator"] else None
            if m["value"] != expected or m["state"] != ("measured" if m["denominator"] else "unmeasured"):
                raise ValueError("Metric measurement state or value mismatch")
    elif name in ("experience-export-record", "experience-collection-record"):
        if value["id"] != digest({k: v for k, v in value.items() if k != "id"}):
            raise ValueError("Export record identity mismatch")
        evaluated = value["profile"] == "evaluated-experience"
        fields = ("result_digest", "trace_digest", "candidate", "verdict", "reward")
        if any((value[k] is not None) != evaluated for k in fields):
            raise ValueError("Export profile evidence mismatch")
        if evaluated and value["reward"] != (1 if value["verdict"] == "pass" else 0):
            raise ValueError("Export reward differs from verdict")
    elif name == "experience-refund-requirement":
        if value["paid_minor"] - value["refunded_minor"] < 2:
            raise ValueError("Refund policy needs at least two minor units remaining")
        if value["order_id"] == "other-order":
            raise ValueError("Order ID conflicts with the negative control")
    elif name == "experience-failure":
        if value["id"] != digest({"source": value["source"]["trace_digest"],
                                  "origin": value["origin"], "evidence": value["evidence"]}):
            raise ValueError("Failure ID does not match its source evidence")
        if digest(value["signature"]) != value["signature_digest"]:
            raise ValueError("Failure signature checksum mismatch")
        for key in ("family_id", "domain", "category", "error_type", "assertion_id", "boundary_ids", "tool_sequence"):
            if value[key] != value["signature"][key]:
                raise ValueError("Failure signature disagrees with " + key)
        if value["eligibility"]["state"] == "eligible" and value["eligibility"]["reasons"]:
            raise ValueError("Eligible failure cannot have blocking reasons")
    elif name == "experience-cluster":
        if value["id"] != digest({"members": sorted(value["member_ids"]), "config_digest": value["config_digest"]}):
            raise ValueError("Cluster ID does not match its membership and configuration")
        if not set(value["representative_ids"]).issubset(value["member_ids"]):
            raise ValueError("Cluster representative is not a member")
        if (value["method"] == "reviewed") != ("review" in value):
            raise ValueError("Reviewed cluster requires review provenance")
    elif name == "experience-cluster-set":
        if digest({"config": value["config"], "overrides": value["overrides"]}) != value["config_digest"]:
            raise ValueError("Clustering configuration checksum mismatch")
        members = []
        for cluster in value["clusters"]:
            validate(cluster)
            if cluster["config_digest"] != value["config_digest"]:
                raise ValueError("Cluster configuration mismatch")
            members.extend(cluster["member_ids"])
        members.extend(x["failure_id"] for x in value["outliers"])
        if len(set(members)) != len(members):
            raise ValueError("Failure assigned more than once")
        if len({x["id"] for x in value["clusters"]}) != len(value["clusters"]):
            raise ValueError("Duplicate cluster ID")
    elif name == "experience-failures":
        for record in value["records"]:
            validate(record)
        if len({x["id"] for x in value["records"]}) != len(value["records"]):
            raise ValueError("Duplicate failure ID")
    elif name == "experience-stage":
        request = {key: value[key] for key in ("stage", "implementation_version", "input_digests", "config", "seed")}
        if digest(request) != value["key"]:
            raise ValueError("Stage key does not match its request")
        if (value["state"] == "completed") != ("output_digest" in value):
            raise ValueError("Only completed stages have an output digest")
        if (value["state"] == "failed") != ("error" in value):
            raise ValueError("Only failed stages have an error")
    elif name == "experience-dataset":
        ids = [x for group in value["splits"].values() for x in group]
        if len(ids) != len(set(ids)):
            raise ValueError("Dataset splits overlap")
    elif name == "experience-labels":
        ids = [x["failure_id"] for x in value["labels"]]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate labeled failure")
    elif name == "experience-verifier":
        if not any(a["op"] not in ("execution_completed", "no_unhandled_error") for a in value["assertions"]):
            raise ValueError("Verifier needs at least one business assertion")
        if value["qualification"]["state"] == "qualified" and not value["qualification"]["evidence_digests"]:
            raise ValueError("Qualified verifier needs qualification evidence")
    elif name == "experience-report":
        validate(value["dataset"])
        validate(value["cluster_set"])
        if digest(value["dataset"]) != value["dataset_digest"] or digest(value["cluster_set"]) != value["cluster_set_digest"]:
            raise ValueError("Report artifact checksum mismatch")
        if value["quality"] is not None:
            validate(value["quality"])
    return value
