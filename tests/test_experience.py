"""Failure-to-Experience A/B acceptance and existing-infrastructure integration."""
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from agent_replay.demo import demo
from agent_replay.experience import cluster_failures, extract_failures, validate
from agent_replay.experience.clustering import evaluate_labels
from agent_replay.experience.contracts import SCHEMAS
from agent_replay.experience.ingestion import normalize_message
from agent_replay.experience.pipeline import cluster, ingest
from agent_replay.experience.reports import write_report
from agent_replay.experience.stages import Stages
from agent_replay.migrations import legacy, to_v2
from agent_replay.regression.assertions import pointer
from agent_replay.schema import validate_trace
from agent_replay.serialization import MISSING, canonical, digest, envelope
from agent_replay.storage import read, save

ROOT = Path(__file__).resolve().parents[1]


def trace(key="one", message="Refund exceeds approval; request_id=req-100", family="refund"):
    return {"schema_version": "2.0", "id": key, "name": "refund-agent",
            "started_at": "2026-01-01T00:00:00Z", "metadata": {"task_family_id": family},
            "execution": {"status": "completed", "duration_ms": 1},
            "input": envelope({"order_id": "o_1042"}), "output": envelope({"ok": True}),
            "capture_health": {"state": "complete"},
            "steps": [{"id": "refund", "name": "payments.refund", "kind": "tool",
                       "boundary_id": "payments.refund", "scope_path": [], "occurrence": 1,
                       "execution": {"status": "completed", "start_ms": 0, "duration_ms": 1},
                       "input": envelope({"order_id": "o_1042", "amount_minor": 29900}),
                       "output": envelope({"ok": True})}],
            "failures": [{"id": "failure-" + key, "category": "assertion", "message": message, "step_id": "refund"}]}


def error_trace(key="error", error_type="ValueError", message="Invalid refund amount", kind="tool"):
    value = trace(key)
    value["failures"] = []
    error = {"type": error_type, "message": message}
    value["steps"][0]["kind"] = kind
    value["steps"][0]["error"] = error
    value["steps"][0]["execution"]["status"] = "error"
    value["steps"][0]["output"] = envelope()
    value["execution"].update(status="error", error=copy.deepcopy(error))
    return value


def records(*traces):
    return [record for value in traces for record in extract_failures(value)]


def labels_for(values, modes):
    return {"schema_version": "experience.labels.v1", "scope": "synthetic",
            "review": {"reviewer": "fixture-author", "reason": "Synthetic known modes"},
            "labels": [{"failure_id": r["id"], "mode": mode} for r, mode in zip(values, modes)]}


class ExtractionTests(unittest.TestCase):
    def test_completed_business_failure_is_observed_without_execution_error(self):
        value = trace()
        before = canonical(value)
        result = extract_failures(value)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["category"], "business_assertion")
        self.assertEqual(result[0]["domain"], "application")
        self.assertEqual(result[0]["step_ids"], ["refund"])
        self.assertEqual(result[0]["eligibility"]["state"], "eligible")
        self.assertEqual(canonical(value), before)

    def test_v1_and_v2_share_signature_but_preserve_distinct_source_digests(self):
        modern = trace()
        old = legacy(modern)
        old.pop("capture_health")
        before = canonical(old)
        a, b = extract_failures(modern)[0], extract_failures(old)[0]
        self.assertEqual(a["signature"], b["signature"])
        self.assertNotEqual(a["source"]["trace_digest"], b["source"]["trace_digest"])
        self.assertEqual(b["eligibility"]["state"], "unknown")
        self.assertEqual(canonical(old), before)

    def test_provider_and_replay_errors_are_separate_from_application_errors(self):
        for error_type, domain, category in (("ProviderError", "infrastructure", "provider_error"),
                                             ("ReplayMismatch", "infrastructure", "replay_mismatch"),
                                             ("RecordedError", "infrastructure", "replay_mismatch"),
                                             ("TimeoutError", "application", "tool_error")):
            with self.subTest(error_type=error_type):
                result = extract_failures(error_trace(error_type=error_type))
                self.assertEqual(len(result), 1)  # propagated execution error isn't another event
                self.assertEqual((result[0]["domain"], result[0]["category"]), (domain, category))

    def test_execution_only_error_and_embedded_step_error(self):
        value = error_trace()
        value["steps"] = []
        self.assertEqual(extract_failures(value)[0]["origin"], "execution_error")
        value = error_trace()
        value["steps"][0]["execution"]["error"] = value["steps"][0].pop("error")
        result = extract_failures(value)[0]
        self.assertEqual(result["evidence"][0]["pointer"], "/steps/0/execution/error")

    def test_error_status_without_exception_details_is_preserved_as_unknown(self):
        value = error_trace()
        value["steps"][0].pop("error")
        value["execution"].pop("error")
        result = extract_failures(value)
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0]["origin"], result[0]["domain"]), ("step_error", "unknown"))
        value["steps"] = []
        result = extract_failures(value)
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0]["origin"], result[0]["domain"]), ("execution_error", "unknown"))

    def test_multi_failure_events_remain_distinct(self):
        value = trace()
        value["failures"].append({"id": "another", "category": "assertion", "message": "Duplicate refund", "step_id": "refund"})
        self.assertEqual(len(extract_failures(value)), 2)

    def test_partial_interrupted_and_redacted_capture_cannot_be_eligible(self):
        value = trace()
        value["execution"]["status"] = "interrupted"
        value["capture_health"]["state"] = "partial"
        result = extract_failures(value)
        self.assertEqual({r["category"] for r in result}, {"business_assertion", "interrupted", "capture_incomplete"})
        self.assertTrue(all(r["eligibility"]["state"] == "blocked" for r in result))
        value = trace()
        value["input"] = envelope({"order_id": "[REDACTED]"})
        self.assertIn("run_input_unavailable", extract_failures(value)[0]["eligibility"]["reasons"])

    def test_unavailable_boundary_observations_block_frozen_replay(self):
        value = trace()
        value["steps"][0]["output"] = envelope()
        self.assertIn("boundary_output_unavailable", extract_failures(value)[0]["eligibility"]["reasons"])

    def test_manual_failure_remains_unknown(self):
        value = trace()
        value["failures"][0]["category"] = "manual"
        self.assertEqual(extract_failures(value)[0]["domain"], "unknown")

    def test_successful_trace_does_not_invent_failure(self):
        value = trace()
        value["failures"] = []
        self.assertEqual(extract_failures(value), [])

    def test_regression_assertions_bind_to_candidate_and_evidence(self):
        value = trace()
        result = {"schema_version": "1.0", "verdict": "fail", "candidate_trace_id": value["id"],
                  "assertions": [{"id": "amount-limit", "verdict": "fail", "evidence": ["refund"],
                                  "expected": 3000, "actual": [29900]}]}
        extracted = extract_failures(value, result)
        failure = next(r for r in extracted if r["origin"] == "regression_assertion")
        self.assertEqual(failure["assertion_id"], "amount-limit")
        self.assertEqual(failure["evidence"], [{"artifact_digest": digest(result), "pointer": "/assertions/0"}])
        result["candidate_trace_id"] = "different"
        with self.assertRaisesRegex(ValueError, "different candidate"):
            extract_failures(value, result)

    def test_error_and_inconclusive_are_not_business_failures(self):
        value = trace()
        value["failures"] = []
        for verdict in ("error", "inconclusive"):
            result = {"schema_version": "1.0", "verdict": verdict, "candidate_trace_id": value["id"],
                      "assertions": [], "reason": "Missing observation"}
            extracted = extract_failures(value, result)
            self.assertEqual(len(extracted), 1)
            self.assertEqual(extracted[0]["domain"], "infrastructure")
            self.assertEqual(extracted[0]["eligibility"]["state"], "blocked")

    def test_source_only_error_result_checks_digest(self):
        value = trace()
        result = {"schema_version": "1.0", "verdict": "error", "source_digest": digest(value)}
        self.assertTrue(extract_failures(value, result))
        result["source_digest"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "different source"):
            extract_failures(value, result)

    def test_bad_trace_and_dangling_evidence_are_rejected(self):
        value = trace()
        value["failures"][0]["step_id"] = "missing"
        with self.assertRaisesRegex(ValueError, "unknown step"):
            extract_failures(value)
        value = trace()
        value["schema_version"] = "9.0"
        with self.assertRaises(ValueError):
            extract_failures(value)

    def test_malformed_metadata_and_contradictory_pass_results_are_rejected(self):
        value = trace()
        value["metadata"] = None
        with self.assertRaisesRegex(ValueError, "metadata"):
            extract_failures(value)
        result = {"schema_version": "1.0", "verdict": "pass", "candidate_trace_id": "one",
                  "assertions": [{"id": "amount", "verdict": "fail"}]}
        with self.assertRaisesRegex(ValueError, "failed assertion"):
            extract_failures(trace(), result)

    def test_volatile_ids_normalize_while_amounts_and_status_codes_survive(self):
        self.assertEqual(normalize_message("Request_id=abc-12 FAILED"), normalize_message("request_id=xyz-99 failed"))
        self.assertEqual(normalize_message("at 2026-01-01T01:02:03Z"), "at <time>")
        self.assertNotEqual(normalize_message("limit 30 HTTP 401"), normalize_message("limit 300 HTTP 403"))


class ClusteringTests(unittest.TestCase):
    def test_membership_and_artifacts_are_independent_of_input_order(self):
        values = records(trace("a"), trace("b", message="Refund exceeds approval; request_id=req-200"),
                         trace("c", message="Duplicate refund"), trace("d", message="Duplicate refund"))
        expected = canonical(cluster_failures(values))
        for seed in range(5):
            shuffled = values[:]
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(canonical(cluster_failures(shuffled)), expected)
        self.assertEqual(len(cluster_failures(values)["clusters"]), 2)

    def test_domains_families_assertions_and_boundaries_do_not_merge(self):
        values = records(trace("a"), trace("b"), trace("other-family", family="shipping"))
        changed = trace("boundary")
        changed["steps"][0]["boundary_id"] = "payments.charge"
        values += records(changed, error_trace("provider", "ProviderError"), error_trace("replay", "ReplayMismatch"))
        result = cluster_failures(values)
        self.assertEqual(len(result["clusters"]), 1)
        self.assertEqual(len(result["outliers"]), 4)

    def test_similar_wording_merges_but_different_numeric_limits_do_not(self):
        a = "Refund amount exceeds the approved maximum for this specific customer order"
        values = records(trace("a", a), trace("b", a + "."))
        self.assertEqual(len(cluster_failures(values)["clusters"]), 1)
        values = records(trace("a", "Approval limit 30 exceeded"), trace("b", "Approval limit 300 exceeded"))
        self.assertEqual(len(cluster_failures(values)["clusters"]), 0)

    def test_complete_link_avoids_transitive_similarity_chain(self):
        base = "refund amount exceeds approved limit for the selected customer order"
        values = records(trace("a", base), trace("b", base + " today"), trace("c", base + " today again"))
        result = cluster_failures(values, {"similarity_threshold": 0.9})
        self.assertTrue(all(len(c["member_ids"]) < 3 for c in result["clusters"]))
        self.assertEqual(sum(len(c["member_ids"]) for c in result["clusters"]) + len(result["outliers"]), 3)

    def test_unknown_domain_is_unassigned(self):
        a, b = trace("a"), trace("b")
        a["failures"][0]["category"] = b["failures"][0]["category"] = "manual"
        result = cluster_failures(records(a, b))
        self.assertEqual(len(result["outliers"]), 2)
        self.assertEqual(result["clusters"], [])

    def test_reviewed_merge_split_preserves_reviewer_and_configuration(self):
        values = records(trace("a"), trace("b"), trace("c", "Duplicate refund"), trace("d", "Duplicate refund"))
        review = {"reviewer": "tester", "reason": "Confirmed common root cause"}
        overrides = {"schema_version": "experience.overrides.v1", "groups": [
            {"member_ids": [r["id"] for r in values], "label": "refund invariant", "review": review}]}
        merged = cluster_failures(values, overrides=overrides)
        self.assertEqual(len(merged["clusters"]), 1)
        self.assertEqual(merged["clusters"][0]["review"], review)
        self.assertEqual(merged["clusters"][0]["method"], "reviewed")
        overrides["groups"] = [{"member_ids": [r["id"]], "label": "reviewed singleton", "review": review} for r in values]
        split = cluster_failures(values, overrides=overrides)
        self.assertEqual(len(split["clusters"]), 4)
        self.assertNotEqual(merged["config_digest"], split["config_digest"])

    def test_bad_duplicate_and_cross_domain_reviews_are_rejected(self):
        values = records(trace("a"), error_trace("b", "ProviderError"))
        group = {"member_ids": [r["id"] for r in values], "label": "unsafe", "review": {"reviewer": "x", "reason": "x"}}
        with self.assertRaisesRegex(ValueError, "domains"):
            cluster_failures(values, overrides={"schema_version": "experience.overrides.v1", "groups": [group]})
        group["member_ids"] = [values[0]["id"]]
        with self.assertRaisesRegex(ValueError, "more than once"):
            cluster_failures(values, overrides={"schema_version": "experience.overrides.v1", "groups": [group, group]})

    def test_invalid_configuration_and_duplicate_records_are_rejected(self):
        values = records(trace())
        for config in ({"similarity_threshold": 0.1}, {"min_cluster_size": True}, {"model": "anything"},
                       {"algorithm_version": "future"}, {"similarity_threshold": float("nan")}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                cluster_failures(values, config)
        with self.assertRaisesRegex(ValueError, "Duplicate failure"):
            cluster_failures(values + values)

    def test_pairwise_quality_and_unmeasured_support(self):
        values = records(trace("a"), trace("b"), trace("c", "Duplicate refund"), trace("d", "Duplicate refund"))
        result = cluster_failures(values)
        labels = labels_for(values, ["amount", "amount", "duplicate", "duplicate"])
        quality = evaluate_labels(result, values, labels)
        self.assertEqual(quality["pairwise_precision"], 1)
        self.assertEqual(quality["pairwise_recall"], 1)
        self.assertEqual(quality["gate"], "pass")
        quality = evaluate_labels(result, values, labels_for(values, ["a", "b", "c", "d"]))
        self.assertIsNone(quality["pairwise_recall"])
        self.assertEqual(quality["gate"], "not_evaluated")
        quality = evaluate_labels(result, values, labels_for(values, ["same"] * 4))
        self.assertLess(quality["pairwise_recall"], 0.8)
        self.assertEqual(quality["gate"], "fail")

    def test_labels_cannot_be_duplicated_or_refer_to_unknown_evidence(self):
        values = records(trace("a"), trace("b"))
        result = cluster_failures(values)
        labels = labels_for(values, ["same", "same"])
        labels["labels"][1]["failure_id"] = labels["labels"][0]["failure_id"]
        with self.assertRaises(ValueError):
            evaluate_labels(result, values, labels)
        labels["labels"][1]["failure_id"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "unknown"):
            evaluate_labels(result, values, labels)

    def test_thousand_events_are_complete_reproducible_and_labeled(self):
        values, modes = [], []
        for mode in range(20):
            for index in range(50):
                values.extend(records(trace(f"{mode}-{index}", f"Invariant mode {mode} failed; request_id=req-{index}")))
                modes.append("synthetic-mode-" + str(mode))
        result = cluster_failures(values)
        self.assertEqual(len(result["clusters"]), 20)
        self.assertEqual(sum(len(c["member_ids"]) for c in result["clusters"]), 1000)
        self.assertEqual(cluster_failures(list(reversed(values))), result)
        quality = evaluate_labels(result, values, labels_for(values, modes))
        self.assertEqual(quality["true_positive_pairs"], 20 * 50 * 49 // 2)
        self.assertEqual(quality["gate"], "pass")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_resume_reuses_outputs_without_rewriting_stage(self):
        values = [trace("a"), trace("b")]
        first = ingest(values, directory=self.root)
        path = Path(first["manifest"])
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        again = ingest(list(reversed(values)), directory=self.root)
        self.assertTrue(again["reused"])
        self.assertEqual(first["key"], again["key"])
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        output = cluster(first["value"], directory=self.root)
        self.assertEqual(output["value"], cluster(first["value"], directory=self.root)["value"])

    def test_failed_and_interrupted_stage_resume_and_keys_pin_version_seed_config(self):
        stages = Stages(self.root)
        dataset = ingest([trace()], directory=self.root)["value"]
        dependency = stages.objects.put(dataset)
        def fail():
            raise RuntimeError("controlled failure")
        with self.assertRaises(RuntimeError):
            stages.run("ingest", "test.v1", [dependency], {}, 0, fail)
        outcome = stages.run("ingest", "test.v1", [dependency], {}, 0, lambda: dataset)
        self.assertEqual(read(outcome["manifest"])["attempt"], 2)
        manifest = read(outcome["manifest"])
        manifest.pop("output_digest")
        manifest["state"] = "running"
        save(outcome["manifest"], manifest)
        recovered = stages.run("ingest", "test.v1", [dependency], {}, 0, lambda: dataset)
        self.assertEqual(read(recovered["manifest"])["attempt"], 3)
        for version, config, seed in (("test.v2", {}, 0), ("test.v1", {"option": 1}, 0), ("test.v1", {}, 1)):
            other = stages.run("ingest", version, [dependency], config, seed, lambda: dataset)
            self.assertNotEqual(other["key"], outcome["key"])

    def test_corrupt_completed_output_and_dependencies_are_not_silently_replaced(self):
        first = ingest([trace()], directory=self.root)
        objects = Stages(self.root).objects
        objects.path(first["output_digest"]).write_text('{}')
        with self.assertRaisesRegex(ValueError, "checksum"):
            ingest([trace()], directory=self.root)
        objects.path(digest(trace())).write_text('{}')
        with self.assertRaisesRegex(ValueError, "checksum"):
            ingest([trace()], directory=self.root)

    def test_invalid_trace_is_quarantined_and_valid_evidence_survives(self):
        value = ingest([trace(), {"schema_version": "unknown"}], directory=self.root)["value"]
        self.assertEqual(value["trace_count"], 1)
        self.assertEqual(len(value["records"]), 1)
        self.assertEqual(len(value["rejections"]), 1)
        rejected = value["rejections"][0]
        self.assertEqual(Stages(self.root).objects.get(rejected["artifact_digest"]), {"schema_version": "unknown"})

    def test_stage_identity_pins_input_roles_and_duplicate_inputs_do_not_inflate_counts(self):
        value = trace()
        result = {"schema_version": "1.0", "verdict": "error", "source_digest": digest(value)}
        mixed = ingest([value, result], directory=self.root)
        bound = ingest([value], result, directory=self.root)
        self.assertNotEqual(mixed["key"], bound["key"])
        self.assertEqual(len(mixed["value"]["rejections"]), 1)
        self.assertFalse(bound["value"]["rejections"])
        duplicate = ingest([value, value], [result, result], directory=self.root)
        self.assertEqual(duplicate["key"], bound["key"])
        self.assertEqual(duplicate["value"]["trace_count"], 1)
        self.assertEqual(duplicate["value"]["result_count"], 1)

    def test_real_regression_results_resolve_to_candidate_not_original(self):
        story = demo(self.root / "demo")
        before = story["before"]["results"][0]
        case = read(story["case"])
        source_path = self.root / "demo" / case["source"]["trace_path"]
        source = read(source_path)
        candidate_path = self.root / "demo" / "before" / "runs" / "traces" / (before["candidate_trace_id"] + ".json")
        candidate = read(candidate_path)
        output = ingest([source, candidate], before, directory=self.root / "experience")["value"]
        assertions = [r for r in output["records"] if r["origin"] == "regression_assertion"]
        self.assertEqual(len(assertions), 1)
        self.assertEqual(assertions[0]["source"]["trace_id"], candidate["id"])
        self.assertNotEqual(assertions[0]["source"]["trace_id"], source["id"])
        self.assertFalse(output["rejections"])
        self.assertEqual([story[k]["exit_code"] for k in ("before", "after", "regressed")], [1, 0, 1])

    def test_unknown_and_ambiguous_candidate_results_are_quarantined(self):
        a, b = trace("same"), trace("same", "Different failure")
        result = {"schema_version": "1.0", "verdict": "fail", "candidate_trace_id": "same",
                  "assertions": [{"id": "amount", "verdict": "fail"}]}
        output = ingest([a, b], result, directory=self.root)["value"]
        self.assertEqual(len(output["rejections"]), 1)
        self.assertEqual(len(output["records"]), 2)

    def test_file_byte_digest_matches_source_only_infrastructure_result(self):
        value = trace()
        file_checksum = hashlib.sha256(json.dumps(value, indent=2).encode()).hexdigest()
        result = {"schema_version": "1.0", "verdict": "error", "source_digest": file_checksum, "reason": "Worker failed"}
        output = ingest([value], result, directory=self.root,
                        source_file_digests={digest(value): [file_checksum]})["value"]
        self.assertFalse(output["rejections"])
        self.assertEqual({r["domain"] for r in output["records"]}, {"application", "infrastructure"})

    def test_report_resolves_original_and_normalized_evidence_and_escapes_text(self):
        values = [trace("a", '<script>alert("x")</script>'), legacy(error_trace("b"))]
        dataset = ingest(values, directory=self.root)["value"]
        groups = cluster(dataset, directory=self.root)["value"]
        reports = write_report(dataset, groups, self.root / "reports", Stages(self.root).objects)
        body = Path(reports["html"]).read_text()
        self.assertNotIn('<script>', body)
        self.assertIn('&lt;script&gt;', body)
        self.assertEqual(read(reports["json"])["summary"]["failures"], 2)
        for record in dataset["records"]:
            for evidence in record["evidence"]:
                artifact = Stages(self.root).objects.get(evidence["artifact_digest"])
                self.assertIsNot(pointer(artifact, evidence["pointer"]), MISSING)

    def test_report_rejects_different_records_and_missing_artifacts(self):
        dataset = ingest([trace("a"), trace("b")], directory=self.root)["value"]
        groups = cluster(dataset, directory=self.root)["value"]
        changed = copy.deepcopy(dataset)
        changed["records"].pop()
        with self.assertRaisesRegex(ValueError, "do not match"):
            write_report(changed, groups, self.root / "reports", Stages(self.root).objects)
        Stages(self.root).objects.path(dataset["records"][0]["source"]["trace_digest"]).unlink()
        with self.assertRaises(OSError):
            write_report(dataset, groups, self.root / "reports", Stages(self.root).objects)

    def test_cli_end_to_end_manifest_resume_and_quality_exit_codes(self):
        sources = self.root / "traces"
        sources.mkdir()
        for key in ("a", "b"):
            save(sources / (key + ".json"), trace(key))
        env = {**os.environ, "PYTHONPATH": str(ROOT / "sdk")}
        def cli(*args):
            return subprocess.run([sys.executable, "-m", "agent_replay.cli", "experience", *map(str, args)],
                                  env=env, capture_output=True, text=True)
        directory = self.root / "experience"
        output = self.root / "failures.json"
        result = cli("ingest", sources, "--directory", directory, "--output", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        first = json.loads(result.stdout)
        again = cli("ingest", sources, "--directory", directory)
        self.assertTrue(json.loads(again.stdout)["reused"])
        result = cli("cluster", first["manifest"], "--directory", directory, "--quality-gate")
        self.assertEqual(result.returncode, 3, result.stderr)
        label_path = save(self.root / "labels.json", labels_for(read(output)["records"], ["same", "same"]))
        result = cli("cluster", output, "--directory", directory, "--labels", label_path, "--quality-gate")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["reports"]["quality"]["gate"], "pass")
        result = cli("ingest", self.root / "missing", "--directory", directory)
        self.assertEqual(result.returncode, 2)
        self.assertIn("error", json.loads(result.stderr))


class ContractTests(unittest.TestCase):
    def test_new_contracts_are_versioned_strict_and_reject_tampering(self):
        value = extract_failures(trace())[0]
        self.assertIs(validate(value), value)
        for key, replacement in (("schema_version", "experience.failure.v999"), ("signature_digest", "0" * 64)):
            changed = {**value, key: replacement}
            with self.assertRaises(ValueError):
                validate(changed)
        with self.assertRaises(ValueError):
            validate({**value, "callable": "os:system"})
        changed = copy.deepcopy(value)
        changed["signature"]["domain"] = "infrastructure"
        changed["signature_digest"] = digest(changed["signature"])
        with self.assertRaises(ValueError):
            validate(changed)

    def test_runtime_and_published_schema_agree(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("Install .[dev] to validate published JSON schemas")
        record = extract_failures(trace())[0]
        clustered = cluster_failures([record], {"min_cluster_size": 1})
        for value in (record, clustered, clustered["clusters"][0]):
            name = next(k for k, s in SCHEMAS.items() if s["properties"]["schema_version"]["const"] == value["schema_version"])
            published = read(ROOT / "schema" / (name + ".schema.json"))
            jsonschema.validate(value, published)
            validate(value)
        for name, schema in SCHEMAS.items():
            jsonschema.Draft202012Validator.check_schema(schema)
            published = read(ROOT / "schema" / (name + ".schema.json"))
            self.assertEqual({k: v for k, v in published.items() if not k.startswith("$")}, schema)

    def test_cluster_partition_integrity_and_stage_states(self):
        values = records(trace("a"), trace("b"))
        result = cluster_failures(values)
        result["outliers"].append({"failure_id": values[0]["id"], "reason": "duplicate"})
        with self.assertRaisesRegex(ValueError, "more than once"):
            validate(result)
        group = copy.deepcopy(cluster_failures(values)["clusters"][0])
        group["member_ids"].pop()
        with self.assertRaisesRegex(ValueError, "membership"):
            validate(group)
        value = {"schema_version": "experience.stage.v1", "key": "0" * 64, "stage": "ingest",
                 "implementation_version": "v1", "input_digests": [], "config": {}, "seed": 0,
                 "state": "completed", "attempt": 1}
        value["key"] = digest({key: value[key] for key in ("stage", "implementation_version", "input_digests", "config", "seed")})
        with self.assertRaisesRegex(ValueError, "output digest"):
            validate(value)
        value.update(state="failed", error="known")
        validate(value)

    def test_future_contracts_are_data_only_and_dataset_splits_cannot_overlap(self):
        sha = "0" * 64
        value = {"schema_version": "experience.environment.v1", "id": sha, "adapter_id": "registered.v1",
                 "initial_state": {}, "fixture_digests": [], "asset_digests": [], "dependency_versions": {},
                 "reset_adapter_id": "reset.v1", "limits": {"timeout_seconds": 1, "max_steps": 10, "max_model_calls": 0}}
        validate(value)
        with self.assertRaises(ValueError):
            validate({**value, "python": "untrusted code"})
        manifest = {"schema_version": "experience.dataset.v1", "profile": "scenario", "schema_versions": ["v1"],
                    "artifacts": [], "splits": {"train": [sha], "validation": [], "test": [sha]},
                    "eligibility": {"state": "eligible", "reasons": []}, "exclusions": [], "generation": {}}
        with self.assertRaisesRegex(ValueError, "overlap"):
            validate(manifest)

    def test_future_verifiers_need_business_rules_and_qualification_evidence(self):
        sha = "0" * 64
        value = {"schema_version": "experience.verifier.v1", "id": sha, "evaluator_id": "assertions.v1",
                 "assertions": [{"id": "complete", "op": "execution_completed"}],
                 "requirement_digests": [sha], "qualification": {"state": "draft", "evidence_digests": []}}
        with self.assertRaisesRegex(ValueError, "business assertion"):
            validate(value)
        value["assertions"].append({"id": "amount", "op": "lte", "expected": 3000})
        validate(value)
        value["qualification"]["state"] = "qualified"
        with self.assertRaisesRegex(ValueError, "qualification evidence"):
            validate(value)
        value["qualification"]["evidence_digests"] = [sha]
        validate(value)

    def test_future_scenario_result_and_manifest_match_published_contracts(self):
        sha = "0" * 64
        scenario = {"schema_version": "experience.scenario.v1", "id": sha, "parent_failure_ids": [sha],
                    "parent_cluster_id": sha, "family_id": "refund", "input": {"order_id": "o1"},
                    "mutation": {"operator_id": "amount-boundary.v1", "parameters": {"limit": 3000}},
                    "seed": 0, "difficulty": "boundary", "environment_digest": sha, "verifier_digest": sha}
        result = {"schema_version": "experience.scenario-result.v1", "id": sha, "scenario_digest": sha,
                  "environment_digest": sha, "verifier_digest": sha, "candidate": {"id": "agent", "version": "v1"},
                  "seed": 0, "trace_digest": sha, "verdict": "inconclusive", "evidence": [],
                  "fidelity": {"level": "unknown"}, "duration_ms": 1, "usage": {}}
        manifest = {"schema_version": "experience.dataset.v1", "profile": "evaluated-experience",
                    "schema_versions": ["experience.scenario.v1"], "artifacts": [{"path": "scenario.json", "sha256": sha}],
                    "splits": {"train": [sha], "validation": [], "test": []},
                    "eligibility": {"state": "unknown", "reasons": ["not_collected"]}, "exclusions": [], "generation": {}}
        for value in (scenario, result, manifest):
            validate(value)
        try:
            import jsonschema
        except ImportError:
            self.skipTest("Install .[dev] to validate published JSON schemas")
        for value, name in ((scenario, "experience-scenario"), (result, "experience-scenario-result"),
                            (manifest, "experience-dataset")):
            jsonschema.validate(value, read(ROOT / "schema" / (name + ".schema.json")))


if __name__ == "__main__":
    unittest.main()
