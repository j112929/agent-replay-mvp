"""Failure-to-Experience commands. Errors use the existing CLI exit code 2."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from ..serialization import digest
from ..storage import read, save
from .contracts import validate
from .ingestion import _result_list
from .pipeline import cluster, ingest
from .reports import write_report
from .stages import Stages


def _paths(arguments):
    paths = set()
    for argument in arguments:
        path = Path(argument)
        if path.is_file():
            paths.add(path.resolve())
        elif path.is_dir():
            paths.update(p.resolve() for p in path.rglob("*.json") if p.is_file())
        else:
            raise ValueError("Input path does not exist: " + str(path))
    if not paths:
        raise ValueError("No JSON inputs found")
    return sorted(paths)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="agent-replay experience", description="Extract and cluster failure evidence locally.")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest_cmd = commands.add_parser("ingest", help="Validate v1/v2 traces and extract observed failure events")
    ingest_cmd.add_argument("sources", nargs="+")
    ingest_cmd.add_argument("--results", nargs="+", help="Candidate regression result or suite JSON files")
    cluster_cmd = commands.add_parser("cluster", help="Cluster failure events and write JSON/HTML evidence reports")
    cluster_cmd.add_argument("dataset", help="Failure dataset or completed ingest manifest")
    cluster_cmd.add_argument("--config", help="Clustering configuration JSON")
    cluster_cmd.add_argument("--overrides", help="Reviewed merge/split groups JSON")
    cluster_cmd.add_argument("--labels", help="Reviewed failure-mode labels for pairwise accuracy")
    cluster_cmd.add_argument("--report-dir")
    cluster_cmd.add_argument("--quality-gate", action="store_true", help="Exit 1 on failed accuracy targets; 3 when unmeasured")
    mutate_cmd = commands.add_parser("mutate", help="Generate offline refund scenarios from explicit policy requirements")
    mutate_cmd.add_argument("dataset")
    mutate_cmd.add_argument("clusters")
    mutate_cmd.add_argument("--cluster-id", required=True)
    mutate_cmd.add_argument("--requirements", required=True)
    mutate_cmd.add_argument("--budget", type=int, default=11)
    loop_cmd = commands.add_parser("refund-loop", help="Qualify refund verifiers and execute fail/pass/fail")
    loop_cmd.add_argument("bundle")
    loop_cmd.add_argument("--source", required=True)
    qualify_cmd = commands.add_parser("qualify", help="Qualify verifier witnesses without running the regression story")
    qualify_cmd.add_argument("bundle")
    qualify_cmd.add_argument("--source", required=True)
    demo_cmd = commands.add_parser("refund-demo", help="Run the full synthetic refund pipeline without keys")
    demo_cmd.add_argument("--requirements")
    demo_cmd.add_argument("--budget", type=int, default=11)
    evaluate_cmd = commands.add_parser("evaluate", help="Compute refund metrics from verified execution evidence")
    evaluate_cmd.add_argument("bundle")
    evaluate_cmd.add_argument("--report", required=True)
    evaluate_cmd.add_argument("--baseline", help="Stored, reproducible prior evaluation for measured mode novelty")
    evaluate_cmd.add_argument("--report-dir")
    export_cmd = commands.add_parser("export", help="Export qualified refund scenarios or evaluated experiences")
    export_cmd.add_argument("bundle")
    export_cmd.add_argument("--report", required=True)
    export_cmd.add_argument("--destination", required=True)
    export_cmd.add_argument("--profile", choices=("scenario", "evaluated-experience", "policy-rollout"), default="evaluated-experience")
    export_cmd.add_argument("--split-ratios", nargs=3, type=float, default=(0.8, 0.1, 0.1))
    verify_cmd = commands.add_parser("verify-export", help="Verify a portable export and every evidence binding")
    verify_cmd.add_argument("dataset")
    families_cmd = commands.add_parser("refund-families", help="Execute independent synthetic refund families and export nonempty splits")
    families_cmd.add_argument("--destination", required=True)
    families_cmd.add_argument("--family-count", type=int, default=12)
    families_cmd.add_argument("--budget", type=int, default=2)
    collection_cmd = commands.add_parser("verify-collection", help="Verify multi-family data and all portable child evidence")
    collection_cmd.add_argument("dataset")
    heldout_cmd = commands.add_parser("held-out", help="Compare registered candidates on validation/test only")
    heldout_cmd.add_argument("dataset")
    models_cmd = commands.add_parser("cross-model", help="Opt-in bounded model runs on validation/test")
    models_cmd.add_argument("dataset")
    models_cmd.add_argument("--config", required=True)
    models_cmd.add_argument("--destination", required=True)
    models_cmd.add_argument("--live", action="store_true")
    model_verify_cmd = commands.add_parser("verify-models", help="Recompute model metrics from execution evidence")
    model_verify_cmd.add_argument("dataset")
    model_verify_cmd.add_argument("--experiments", required=True)
    for command in (families_cmd, collection_cmd, heldout_cmd, models_cmd, model_verify_cmd, ingest_cmd, cluster_cmd, mutate_cmd, loop_cmd, qualify_cmd, demo_cmd, evaluate_cmd, export_cmd, verify_cmd):
        command.add_argument("--directory", default=".replay/experience", help="Shared stage and evidence directory")
        command.add_argument("--seed", type=int, default=0)
        command.add_argument("--output", help="Also save a JSON sidecar")
    args = parser.parse_args(argv)
    try:
        if args.command in ("refund-families", "verify-collection", "held-out", "cross-model", "verify-models"):
            from .collection import demo as families_demo, export_collection, validate_collection, held_out
            from .models import run_models, evaluate_models
            if args.output:
                protected = getattr(args, 'dataset', None) or getattr(args, 'destination', None)
                if protected and Path(args.output).resolve().is_relative_to(Path(protected).resolve()):
                    raise ValueError('--output must be outside the immutable dataset')
            if args.command == "refund-families":
                runs = families_demo(args.directory, seed=args.seed, budget=args.budget, family_count=args.family_count)
                value = export_collection(runs, args.destination, seed=args.seed)
                response = {"record_count": value['record_count'], "splits": {k: len(v) for k,v in value['splits'].items()}, "destination": str(Path(args.destination).resolve())}
            elif args.command == "verify-collection":
                value = validate_collection(args.dataset)['manifest']
                response = {"verified": True, "record_count": value['record_count']}
            elif args.command == "held-out":
                value = held_out(args.dataset)
                response = value
            elif args.command == "cross-model":
                config = read(args.config)
                if not args.live:
                    raise ValueError('Model provider calls require explicit --live; default CI remains offline')
                value = run_models(args.dataset, config, args.destination)
                response = value
            else:
                value = evaluate_models(args.dataset, args.experiments)
                response = value
            code = 0
        elif args.command in ("evaluate", "export", "verify-export"):
            if args.output and args.command in ("export", "verify-export"):
                destination = args.destination if args.command == "export" else args.dataset
                if Path(args.output).resolve().is_relative_to(Path(destination).resolve()):
                    raise ValueError("--output must be outside the immutable exported dataset")
            from .evaluation import evaluate, write_evaluation
            from .export import export_dataset, validate_export
            if args.command == "verify-export":
                value = validate_export(args.dataset)
                response = {"manifest_digest": digest(value), "record_count": value["generation"]["record_count"], "verified": True}
            elif args.command == "evaluate":
                value = evaluate(read(args.bundle), read(args.report, limit=100*1024*1024), directory=args.directory,
                                 baseline=read(args.baseline) if args.baseline else None)
                response = {"evaluation_digest": digest(value), "metrics": value["metrics"],
                            "reports": write_evaluation(value, args.report_dir or Path(args.directory) / "evaluation")}
            else:
                response = export_dataset(read(args.bundle), read(args.report, limit=100*1024*1024), args.destination,
                                          directory=args.directory, profile=args.profile, seed=args.seed, ratios=args.split_ratios)
                value = validate_export(args.destination)
            code = 0
        elif args.command in ("refund-demo", "mutate", "refund-loop", "qualify"):
            from .pipeline import mutate
            from .compiler import refund_loop, qualify
            from .mutation import DEFAULT_REQUIREMENT
            if args.command == "refund-demo":
                from ..demo import demo
                demo_root = Path(args.directory).resolve() / "seed"
                story = demo(demo_root)
                source = read(demo_root / read(story["case"])["source"]["trace_path"])
                dataset = ingest([source], directory=args.directory)["value"]
                clusters = cluster(dataset, directory=args.directory, config={"min_cluster_size": 1})["value"]
                parent = next(c for c in clusters["clusters"] if c["domain"] == "application")
                outcome = mutate(dataset, parent, read(args.requirements) if args.requirements else DEFAULT_REQUIREMENT,
                                 directory=args.directory, seed=args.seed, budget=args.budget)
                save(Path(args.directory) / "scenarios.json", outcome["value"])
                value = refund_loop(outcome["value"], source, args.directory)
            elif args.command == "mutate":
                dataset = validate(read(args.dataset), "experience-failures")
                clusters = validate(read(args.clusters), "experience-cluster-set")
                if clusters["records_digest"] != digest(dataset["records"]):
                    raise ValueError("Cluster set differs from failure dataset")
                parent = next((c for c in clusters["clusters"] if c["id"] == args.cluster_id), None)
                if parent is None:
                    raise ValueError("Unknown cluster ID")
                outcome = mutate(dataset, parent, read(args.requirements), directory=args.directory,
                                 seed=args.seed, budget=args.budget)
                value = outcome["value"]
            elif args.command == "qualify":
                value = qualify(read(args.bundle), read(args.source), args.directory)
            else:
                value = refund_loop(read(args.bundle), read(args.source), args.directory)
            response = {"command": args.command, "directory": str(Path(args.directory).resolve())}
            if args.command == "mutate":
                response.update({k: v for k, v in outcome.items() if k != "value"})
                response["scenario_count"] = len(value["entries"])
            elif args.command == "qualify":
                response.update(scenario_count=len(value), qualified_count=sum(q["state"] == "qualified" for q in value.values()))
            else:
                response.update({k: value[k] for k in ("scenario_count", "qualified_count", "exit_codes", "metrics")})
                response["report"] = str(Path(args.directory).resolve() / "report.html")
            code = 1 if args.command == "qualify" and any(q["state"] != "qualified" for q in value.values()) else 0
        elif args.command == "ingest":
            traces, checksums = [], {}
            for path in _paths(args.sources):
                if path.stat().st_size > 5 * 1024 * 1024:
                    raise ValueError("Trace exceeds 5 MB")
                data = path.read_bytes()
                if len(data) > 5 * 1024 * 1024:
                    raise ValueError("Trace exceeds 5 MB")
                trace = json.loads(data)
                traces.append(trace)
                checksums.setdefault(digest(trace), []).append(hashlib.sha256(data).hexdigest())
            results = []
            if args.results:
                for path in _paths(args.results):
                    results.extend(_result_list(read(path)))
            outcome = ingest(traces, results, directory=args.directory, seed=args.seed, source_file_digests=checksums)
            value = outcome["value"]
            code = 2 if value["rejections"] else 0
            response = {key: item for key, item in outcome.items() if key != "value"}
            response.update(trace_count=value["trace_count"], failure_count=len(value["records"]),
                            rejections=value["rejections"])
        else:
            value = read(args.dataset, limit=100 * 1024 * 1024)
            if isinstance(value, dict) and value.get("schema_version") == "experience.stage.v1":
                validate(value)
                if value["stage"] != "ingest" or value["state"] != "completed":
                    raise ValueError("Supply a completed ingest manifest")
                value = Stages(args.directory).objects.get(value["output_digest"])
            dataset = validate(value, "experience-failures")
            outcome = cluster(dataset, directory=args.directory, seed=args.seed,
                              config=read(args.config) if args.config else None,
                              overrides=read(args.overrides) if args.overrides else None)
            value = outcome["value"]
            stages = Stages(args.directory)
            labels = read(args.labels) if args.labels else None
            report_key = digest({"dataset": digest(dataset), "clusters": digest(value), "labels": labels,
                                 "renderer_version": "experience-report.v1"})
            reports = write_report(dataset, value, args.report_dir or stages.directory / "reports" / report_key,
                                   stages.objects, labels=labels)
            response = {key: item for key, item in outcome.items() if key != "value"}
            response["reports"] = reports
            quality = reports["quality"]
            code = 0
            if args.quality_gate:
                code = 3 if quality is None or quality["gate"] == "not_evaluated" else 1 if quality["gate"] == "fail" else 0
        if args.output:
            save(args.output, value)
            response["output"] = str(Path(args.output).resolve())
        print(json.dumps(response, ensure_ascii=False))
        return code
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
