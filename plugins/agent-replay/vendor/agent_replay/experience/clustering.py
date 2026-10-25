"""Deterministic exact signatures followed by conservative complete-link merging."""
from collections import Counter, defaultdict
from difflib import SequenceMatcher
import re

from ..serialization import digest
from .contracts import CONFIG, _validate, validate

ALGORITHM_VERSION = "deterministic.v1"
DEFAULT_CONFIG = {"algorithm_version": ALGORITHM_VERSION,
                  "similarity_threshold": 0.85, "min_cluster_size": 2}


def resolve_config(config=None):
    if config is not None and not isinstance(config, dict):
        raise ValueError("Clustering config must be an object")
    value = {**DEFAULT_CONFIG, **(config or {})}
    digest(value)  # Reject non-portable numbers before similarity comparisons.
    _validate(value, CONFIG, "config")
    return value


def records_digest(records):
    return digest(sorted(records, key=lambda x: x["id"]))


def _anchors(record):
    return digest({k: v for k, v in record["signature"].items() if k != "message"})


def _similarity(left, right):
    if _anchors(left) != _anchors(right):
        return 0.0
    a, b = left["signature"]["message"], right["signature"]["message"]
    if a == b:
        return 1.0
    # Numeric tokens stay exact: different limits/status codes are distinct.
    if re.findall(r"\b\d+(?:\.\d+)?\b", a) != re.findall(r"\b\d+(?:\.\d+)?\b", b):
        return 0.0
    words_a, words_b = set(a.split()), set(b.split())
    overlap = len(words_a & words_b) / max(1, len(words_a | words_b))
    return round(0.5 * overlap + 0.5 * SequenceMatcher(None, a, b, autojunk=False).ratio(), 8)


def cluster_failures(records, config=None, overrides=None):
    """Partition records exactly once; input order cannot change membership.

    Review groups replace assignments for their named members, enabling both
    merge and split. All remaining records are clustered automatically.
    """
    if not isinstance(records, list):
        raise ValueError("Failure records must be a list")
    for record in records:
        validate(record, "experience-failure")
    lookup = {record["id"]: record for record in records}
    if len(lookup) != len(records):
        raise ValueError("Duplicate failure ID")
    config = resolve_config(config)
    override_doc = overrides if overrides is not None else {"schema_version": "experience.overrides.v1", "groups": []}
    validate(override_doc, "experience-overrides")
    groups = sorted([{**group, "member_ids": sorted(group["member_ids"])}
                     for group in override_doc["groups"]], key=digest)
    config_digest = digest({"config": config, "overrides": groups})
    assigned = set()
    clusters, outliers = [], []

    def make_cluster(members, method="automatic", label=None, review=None):
        members = sorted(members, key=lambda x: x["id"])
        # Compare unique signatures so a large exact group stays linear.
        signatures = sorted({r["signature_digest"]: r for r in members}.values(), key=lambda x: x["id"])
        confidence = min((_similarity(a, b) for i, a in enumerate(signatures)
                          for b in signatures[i+1:]), default=1.0)
        representatives = sorted(signatures, key=lambda x: x["id"])[:3]
        ids = [record["id"] for record in members]
        first = members[0]
        cluster = {"schema_version": "experience.cluster.v1",
                   "id": digest({"members": ids, "config_digest": config_digest}),
                   "label": label or first["category"] + ": " + (first["signature"]["message"] or first["error_type"] or "observed failure"),
                   "domain": first["domain"], "family_id": first["family_id"],
                   "member_ids": ids, "representative_ids": [r["id"] for r in representatives],
                   "confidence": confidence, "method": method, "config_digest": config_digest}
        if review is not None:
            cluster["review"] = review
        clusters.append(validate(cluster))

    for group in groups:
        ids = set(group["member_ids"])
        if not ids.issubset(lookup):
            raise ValueError("Review references an unknown failure")
        if ids & assigned:
            raise ValueError("Review assigns a failure more than once")
        members = [lookup[x] for x in sorted(ids)]
        if len({(r["domain"], r["family_id"]) for r in members}) != 1:
            raise ValueError("Review cannot merge different domains or task families")
        make_cluster(members, "reviewed", group["label"], group["review"])
        assigned.update(ids)

    compatible = defaultdict(lambda: defaultdict(list))
    for record in sorted(records, key=lambda x: x["id"]):
        if record["id"] in assigned:
            continue
        if record["domain"] == "unknown":
            outliers.append({"failure_id": record["id"], "reason": "unclassified_failure_domain"})
            continue
        compatible[_anchors(record)][record["signature_digest"]].append(record)

    for anchor in sorted(compatible):
        partitions = []
        for signature in sorted(compatible[anchor]):
            exact = compatible[anchor][signature]
            representative = exact[0]
            # Complete-link avoids merging A~B~C when A and C disagree.
            for partition in partitions:
                if all(_similarity(representative, prior[0]) >= config["similarity_threshold"] for prior in partition):
                    partition.append(exact)
                    break
            else:
                partitions.append([exact])
        for partition in partitions:
            members = [r for exact in partition for r in exact]
            if len(members) < config["min_cluster_size"]:
                outliers.extend({"failure_id": r["id"], "reason": "insufficient_similar_evidence"} for r in members)
            else:
                make_cluster(members)
    return validate({"schema_version": "experience.cluster-set.v1", "records_digest": records_digest(records),
                     "config": config, "config_digest": config_digest, "overrides": groups,
                     "clusters": sorted(clusters, key=lambda x: x["id"]),
                     "outliers": sorted(outliers, key=lambda x: x["failure_id"])})


def evaluate_labels(cluster_set, records, labels):
    """Pairwise clustering accuracy on supplied reviewed labels (no LLM judge).

    Outliers are individual predicted groups. Counts use a contingency table,
    avoiding quadratic enumeration. Dependent pairs are not independent trials.
    """
    validate(cluster_set, "experience-cluster-set")
    validate(labels, "experience-labels")
    if cluster_set["records_digest"] != records_digest(records):
        raise ValueError("Quality evaluation records do not match the cluster set")
    lookup = {r["id"]: r for r in records}
    prediction = {member: cluster["id"] for cluster in cluster_set["clusters"] for member in cluster["member_ids"]}
    prediction.update({item["failure_id"]: "outlier:" + item["failure_id"] for item in cluster_set["outliers"]})
    cells, predicted, gold = Counter(), Counter(), Counter()
    for label in labels["labels"]:
        key = label["failure_id"]
        if key not in lookup or key not in prediction:
            raise ValueError("Label references an unknown or unassigned failure")
        cells[(prediction[key], label["mode"])] += 1
        predicted[prediction[key]] += 1
        gold[label["mode"]] += 1
    pairs = lambda counts: sum(n * (n - 1) // 2 for n in counts.values())
    true_positive, predicted_pairs, gold_pairs = pairs(cells), pairs(predicted), pairs(gold)
    precision = true_positive / predicted_pairs if predicted_pairs else None
    recall = true_positive / gold_pairs if gold_pairs else None
    measured = precision is not None and recall is not None
    return validate({"schema_version": "experience.cluster-quality.v1", "labels_digest": digest(labels),
            "review": labels["review"], "scope": labels["scope"], "labeled_records": len(labels["labels"]),
            "unlabeled_records": len(records) - len(labels["labels"]),
            "true_positive_pairs": true_positive, "false_positive_pairs": predicted_pairs - true_positive,
            "false_negative_pairs": gold_pairs - true_positive,
            "pairwise_precision": precision, "pairwise_recall": recall,
            "targets": {"pairwise_precision": 0.9, "pairwise_recall": 0.8},
            "gate": "not_evaluated" if not measured else "pass" if precision >= 0.9 and recall >= 0.8 else "fail",
            "uncertainty": "Not estimated; pairs share records. Validate on an independently adjudicated corpus."})
