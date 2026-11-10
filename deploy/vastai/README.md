# Vast.ai GPU smoke deployment

This is a single-host correctness run for the existing vLLM -> reward -> GRPO
pipeline. It does not provision or charge for a Vast instance, and GPU execution
has not been validated until the target run produces `acceptance.json` with
`passed: true`. It does not deploy the public debugger website.

## Instance configuration

- On-demand, verified host; review reliability, maximum rental duration,
  bandwidth prices and the complete disk-inclusive hourly quote.
- Four 24 GB-class GPUs on one machine (3090/4090), at least four allocated CPU
  cores, preferably 64 GB system RAM, and 100 GB container storage.
- Image: `vllm/vllm-openai:v0.26.0`, using Vast's SSH launch mode. The container
  is the runtime; no nested Docker or Kubernetes is required.
- Use a storage path that survives stopping the instance. Container-local data
  is lost when the instance is destroyed; export results and checkpoints first.
- Keep the controller and Ray local. No public API, dashboard or Ray port needs
  to be exposed for this single-host test.

## Run after connecting over SSH

Clone this deployment branch, then run from its root:

```sh
git clone --branch deploy/vastai-gpu-smoke --single-branch https://github.com/j112929/agent-replay-mvp.git
cd agent-replay-mvp
bash deploy/vastai/bootstrap.sh --directory /workspace/replay-data/smoke-001 --gpus 4
```

The script downloads the public `Qwen/Qwen2.5-0.5B-Instruct` safetensors model,
records the resolved model commit, copies and seals an immutable checkpoint,
and runs two updates. It uses two rollout Actors and a two-GPU DDP Learner.
It reduces batch size, prompt group size and sequence length for first-run
memory headroom. This smoke workload is not a representative benchmark.

For a cheaper two-GPU pipeline check, use `--gpus 2`. That reserves one rollout
GPU and one learner GPU and does **not** validate multi-GPU DDP. Reuse neither
run directories nor sealed checkpoints for unrelated experiments.

For a local existing model, pass `--checkpoint /absolute/model/path`. A
dependency-light configuration check is available without CUDA:

```sh
python3 deploy/vastai/gpu_smoke.py --directory /tmp/replay-profile-check \
  --checkpoint /absolute/model/path --gpus 4 --prepare-only
```

Inspect `run.log`, `hardware.json`, `provenance.json`, `run/benchmark.json`,
`checkpoints/` and `acceptance.json` beneath the run directory. Passing checks
require committed policy versions, generated tokens, consumed rollouts,
immutable training checkpoints, finite loss, and the expected learner world
size. Uniform rewards can yield zero GRPO advantages; passage is not evidence
of improved reward or model quality. Repeated vLLM model loading is included.

The driver has a bounded execution timeout, but exiting a process does not stop
Vast GPU billing. Stop the instance after collecting evidence; storage charges
continue while stopped. Destroy only after the results have been exported.
