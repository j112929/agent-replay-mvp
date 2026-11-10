"""Prepare and run a bounded single-host vLLM -> GRPO GPU smoke test."""
import argparse
import importlib.metadata
import json
import math
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import threading

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sdk"))


def stream_log(path, stop):
    """Mirror the retained driver log into container logs without changing its pipes."""
    with path.open(errors="replace") as source:
        while True:
            chunk = source.read(8192)
            if chunk:
                print(chunk, end="", flush=True)
            elif stop.wait(0.2):
                print(source.read(), end="", flush=True)
                return


def preflight(gpus):
    import torch

    if not torch.cuda.is_available() or torch.cuda.device_count() < gpus:
        raise RuntimeError(f"Need {gpus} visible CUDA GPUs")
    devices = [torch.cuda.get_device_properties(i) for i in range(gpus)]
    if any(device.total_memory < 23 * 1024**3 for device in devices):
        raise RuntimeError("The smoke profile expects at least 24 GB-class GPUs")
    versions = {name: importlib.metadata.version(name)
                for name in ("torch", "transformers", "vllm", "ray")}
    if versions["vllm"].split(".")[:2] != ["0", "26"]:
        raise RuntimeError("Use the repository's vLLM 0.26.x runtime")
    return {"dependencies": versions, "cuda": torch.version.cuda,
            "devices": [{"name": d.name, "memory_bytes": d.total_memory}
                        for d in devices]}


def prepare(args):
    from agent_replay.rollout.artifacts import seal_checkpoint, verify_checkpoint

    directory = args.directory.resolve()
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Use a new empty directory; smoke runs never implicitly resume")
    directory.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(directory).free < 30 * 1024**3 and not args.prepare_only:
        raise RuntimeError("At least 30 GiB free space is required for this smoke test")
    provenance = {"purpose": "correctness smoke test; no performance or reward-gain claim"}
    if args.checkpoint:
        source = args.checkpoint.resolve()
        provenance["source_checkpoint"] = str(source)
    else:
        if args.prepare_only:
            raise ValueError("--prepare-only requires an existing --checkpoint")
        from huggingface_hub import HfApi, snapshot_download

        revision = HfApi(token=False).model_info(args.model, revision=args.revision).sha
        source = Path(snapshot_download(
            args.model, revision=revision, token=False,
            cache_dir=str(directory.parent / "hf-cache"),
            allow_patterns=["*.json", "*.safetensors", "*.model", "*.txt", "*.tiktoken"],
        ))
        provenance.update(model=args.model, model_revision=revision)
    if not source.is_dir() or not (source / "config.json").is_file():
        raise ValueError("Expected a local Hugging Face checkpoint with config.json")
    if not list(source.glob("*.safetensors")):
        raise ValueError("Use a safetensors checkpoint")
    if source == directory or source in directory.parents or directory in source.parents:
        raise ValueError("Source checkpoint and run directory must be separate")
    root = directory / "checkpoints"
    initial = root / "initial"
    shutil.copytree(source, initial, ignore=shutil.ignore_patterns(".cache", "manifest.json"))
    reference = seal_checkpoint(initial)
    verify_checkpoint(reference, root)
    profile = json.loads((REPO / "examples/benchmarks/gpu-grpo.json").read_text())
    llm = json.loads((REPO / "examples/benchmarks/llm-config.json").read_text())
    learner_gpus = 2 if args.gpus == 4 else 1
    profile.update(actors=args.gpus - learner_gpus, actor_gpus=1,
                   learner_gpus=learner_gpus, updates=args.updates,
                   batch_size=1, max_inflight=1, max_policy_lag=0,
                   timeout_seconds=args.timeout_seconds, initial_weights=reference)
    llm.update(checkpoint_root=str(root), learner_processes=learner_gpus,
               group_size=2, max_model_len=256, max_new_tokens=16,
               timeout_seconds=min(args.timeout_seconds, 900))
    paths = {"profile": directory / "gpu-grpo.json", "llm": directory / "llm-config.json"}
    for key, value in (("profile", profile), ("llm", llm)):
        paths[key].write_text(json.dumps(value, indent=2) + "\n")
    (directory / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    return directory, paths, profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--gpus", type=int, choices=(2, 4), default=4)
    parser.add_argument("--updates", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    parser.add_argument("--revision", default="main")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if args.updates < 1 or not 60 <= args.timeout_seconds <= 7200:
        parser.error("updates must be positive and timeout must be 60..7200 seconds")
    hardware = None if args.prepare_only else preflight(args.gpus)
    directory, paths, profile = prepare(args)
    print(json.dumps({"prepared": True, "directory": str(directory),
                      "gpu_count": args.gpus, "executed": False}), flush=True)
    if args.prepare_only:
        return
    (directory / "hardware.json").write_text(json.dumps(hardware, indent=2) + "\n")
    env = dict(os.environ, ROLLOUT_LLM_CONFIG=str(paths["llm"]),
               TOKENIZERS_PARALLELISM="false", OMP_NUM_THREADS="1",
               VLLM_NO_USAGE_STATS="1", DO_NOT_TRACK="1", PYTHONUNBUFFERED="1")
    result_path = directory / "acceptance.json"
    try:
        with (directory / "run.log").open("w") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "agent_replay.cli", "rollout", "async-run",
                 "--config", str(paths["profile"]), "--directory", str(directory / "run")],
                stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True,
            )
            stop_stream = threading.Event()
            stream = threading.Thread(target=stream_log,
                                      args=(directory / "run.log", stop_stream), daemon=True)
            stream.start()
            try:
                returncode = process.wait(timeout=args.timeout_seconds + 180)
            except BaseException:
                # Give the driver's finally block a chance to close Ray workers.
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise
            finally:
                stop_stream.set()
                stream.join(timeout=5)
            if returncode:
                raise RuntimeError(f"GPU driver exited with status {returncode}; inspect run.log")
        report = json.loads((directory / "run/benchmark.json").read_text())
        config = report["config"]
        if config["final_policy_version"] - config["initial_policy_version"] != args.updates:
            raise RuntimeError("Expected policy updates were not committed")
        if not report.get("generated_tokens") or not report.get("learner_consumed_rollouts"):
            raise RuntimeError("Missing rollout generation or learner consumption evidence")
        checkpoints = list((directory / "checkpoints").glob("update-*/training-metrics.json"))
        if len(checkpoints) < args.updates:
            raise RuntimeError("Missing training checkpoints")
        for path in checkpoints:
            metrics = json.loads(path.read_text())
            if metrics["world_size"] != profile["learner_gpus"] or not math.isfinite(metrics["grpo_loss"]):
                raise RuntimeError("Invalid learner metrics")
        result = {"passed": True, "gpu_count": args.gpus, "updates": args.updates,
                  "ddp_world_size": profile["learner_gpus"],
                  "generated_tokens": report["generated_tokens"],
                  "note": "Smoke acceptance only; does not establish training improvement"}
    except Exception as error:
        result_path.write_text(json.dumps({"passed": False, "error": str(error)}, indent=2) + "\n")
        raise
    result_path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
