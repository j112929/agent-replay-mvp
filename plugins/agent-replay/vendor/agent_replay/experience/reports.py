"""Self-contained JSON and escaped HTML evidence reports."""
from collections import Counter
import html
import os
from pathlib import Path
from urllib.parse import quote

from ..regression.assertions import pointer
from ..serialization import MISSING, digest
from ..storage import save
from .clustering import evaluate_labels, records_digest
from .contracts import validate


def write_report(dataset, cluster_set, directory, objects, labels=None):
    validate(dataset, "experience-failures")
    validate(cluster_set, "experience-cluster-set")
    if records_digest(dataset["records"]) != cluster_set["records_digest"]:
        raise ValueError("Report records do not match the cluster set")
    membership = [key for c in cluster_set["clusters"] for key in c["member_ids"]]
    membership.extend(item["failure_id"] for item in cluster_set["outliers"])
    if set(membership) != {r["id"] for r in dataset["records"]}:
        raise ValueError("Cluster set is not a complete partition")
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    lookup = {r["id"]: r for r in dataset["records"]}
    for group in cluster_set["clusters"]:
        if any((lookup[key]["domain"], lookup[key]["family_id"]) != (group["domain"], group["family_id"])
               for key in group["member_ids"]):
            raise ValueError("Cluster domain or family disagrees with its members")
    # Reports resolve every pointer, making evidence completeness reviewable.
    for record in dataset["records"]:
        for evidence in record["evidence"]:
            artifact = objects.get(evidence["artifact_digest"])
            if pointer(artifact, evidence["pointer"]) is MISSING:
                raise ValueError("Failure evidence pointer is unavailable")
    quality = evaluate_labels(cluster_set, dataset["records"], labels) if labels is not None else None
    if labels is not None:
        objects.put(labels)
    report = {"schema_version": "experience.report.v1", "dataset_digest": digest(dataset),
              "cluster_set_digest": digest(cluster_set), "summary": {
                  "traces": dataset["trace_count"], "failures": len(dataset["records"]),
                  "clusters": len(cluster_set["clusters"]), "outliers": len(cluster_set["outliers"]),
                  "rejections": len(dataset["rejections"]),
                  "domains": dict(sorted(Counter(r["domain"] for r in dataset["records"]).items()))},
              "dataset": dataset, "cluster_set": cluster_set, "quality": quality}
    json_path = save(directory / "report.json", validate(report))
    esc = lambda value: html.escape(str(value), quote=True)

    def record_html(record):
        links = []
        for evidence in record["evidence"]:
            path = os.path.relpath(objects.path(evidence["artifact_digest"]), directory)
            links.append('<a href="' + esc(quote(path)) + '">' + esc(evidence["artifact_digest"][:12]) + '</a> ' + esc(evidence["pointer"]))
        return ('<li><code>' + esc(record["id"]) + '</code><p>' + esc(record["message"]) + '</p>'
                '<p>' + esc(record["category"]) + ' · capture ' + esc(record["eligibility"]["state"]) +
                ' · ' + esc(", ".join(record["eligibility"]["reasons"])) + '</p><p>' + '<br>'.join(links) + '</p></li>')

    sections = []
    for group in cluster_set["clusters"]:
        sections.append('<section><h2>' + esc(group["label"]) + '</h2><p>' + esc(group["domain"]) +
                        ' · ' + esc(group["family_id"]) + ' · similarity ' + esc(group["confidence"]) +
                        ' · ' + esc(group["method"]) + '</p>' +
                        ('<p>Review: ' + esc(group["review"]) + '</p>' if "review" in group else '') +
                        '<ul>' + ''.join(record_html(lookup[key]) for key in group["member_ids"]) + '</ul></section>')
    sections.append('<section><h2>Unassigned evidence</h2><ul>' + ''.join(
        '<li>' + esc(item["reason"]) + '<ul>' + record_html(lookup[item["failure_id"]]) + '</ul></li>'
        for item in cluster_set["outliers"]) + '</ul></section>')
    sections.append('<section><h2>Rejected inputs</h2><ul>' + ''.join(
        '<li><code>' + esc(item["artifact_digest"]) + '</code> ' + esc(item["reason"]) + '</li>'
        for item in dataset["rejections"]) + '</ul></section>')
    html_path = directory / "report.html"
    # Match storage.save's atomic replacement for the HTML text artifact.
    import tempfile
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write('<!doctype html><html lang="en"><meta charset="utf-8"><title>Failure evidence</title>'
                         '<meta name="viewport" content="width=device-width,initial-scale=1">'
                         '<style>body{font:16px system-ui;max-width:1000px;margin:40px auto;padding:0 20px}'
                         'code{overflow-wrap:anywhere}section{border-top:1px solid #ccc;margin-top:24px}'
                         'li{margin-bottom:20px}p{white-space:pre-wrap}</style><h1>Failure evidence</h1>'
                         '<p>' + esc(report["summary"]) + '</p><p>Configuration: ' + esc(cluster_set["config"]) + '</p>'
                         '<p>Confidence measures signature similarity, not verified root cause.</p>'
                         '<p>Quality: ' + esc(quality if quality is not None else "No adjudicated labels supplied; accuracy unmeasured.") + '</p>'
                         ''.join(sections) + '</html>')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, html_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"json": str(json_path), "html": str(html_path), "summary": report["summary"], "quality": quality}
