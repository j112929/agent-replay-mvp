# Distributed training and benchmark guide (v0.4)

## What is implemented

The existing durable controller now supports persistent process or Ray workers, bounded asynchronous scheduling, explicitly bounded policy lag, content-addressed trajectory storage (local or S3-compatible), online controller backup/restore, authenticated Prometheus metrics, and a vLLM → grouped rewards → PyTorch DDP GRPO → immutable checkpoint path.

The code is not a claim of measured GPU performance. No GPU/cluster was provided for this release. CPU acceptance and GPU/cluster readiness are reported separately. The vLLM/CUDA/NCCL path and Kubernetes manifests require validation on the target hardware before production use.

| Capability | Concrete implementation | Boundary |
| --- | --- | --- |
| Ray | Resource-reserved persistent actors, separate verifier and learner actors, bounded actor restarts; same lease fencing | One learner actor owns a single-node torchrun DDP job; remote workers require reachable TLS controller |
| Kubernetes | KubeRay RayCluster, single-replica TLS controller/PVC, benchmark Job and Prometheus config | Templates require operator, images, GPU plugin, private TLS, role secrets, shared checkpoint and report PVCs; no cluster provisioned |
| PyTorch Distributed | Real DDP gradients with Gloo on CPU or NCCL on CUDA, torchrun ranks | Single-node DDP launcher; no FSDP or multi-node optimizer sharding |
| vLLM | Batched prompt-group generation, token IDs and behavior log probabilities, checkpoint digest verification | Baseline creates one pinned-checkpoint engine process per job; model load is included in measured latency; no hot weight transfer yet |
| GRPO | Per-prompt normalized group advantages, token-level clipped importance ratios, completion masking, equal-completion DDP weighting | Exact-answer reward adapter, beta=0 in bundled learner; objective helper supports explicit reference KL but trainer does not load a reference model |
| Async rollout | Persistent actors/verifiers/learner overlap; bounded unfinished/available work, max policy lag and source-policy-consistent batches | Lag defaults to zero; stale data is allowed only explicitly, GRPO retains behavior probabilities |
| Trajectory storage | SHA-256 addressed immutable JSON objects and database indices; optional boto3 S3 backend | Controller remains single SQLite WAL writer; object garbage collection not automated |
| Checkpoint/recovery | Model, tokenizer, optimizer and RNG state; hashes; atomic directory publication; identical batch retry reuses completed checkpoint; controller backup revokes live leases | Checkpoints must remain on shared durable storage; controller backup includes trajectory objects but not multi-GB model directories |
| Observability | Worker busy/idle/claim times, completions, generated tokens, sample consumption, E2E latency histograms, optional nvidia-smi sampling | No fabricated GPU samples; telemetry can be incomplete if worker dies before reporting its final interval |

## Dependencies

The base package stays dependency-light. CPU integration validation uses Python 3.12, PyTorch 2.14.0, Ray 2.58.0 and Transformers 5.17.0. GPU images use the vLLM 0.26.0 base image and its PyTorch dependency set; those GPU combinations have not been executed here.

```sh
pip install -e '.[training,cluster,storage]'
# CUDA/vLLM runtime: use a compatible Linux GPU environment.
pip install -e '.[rollout-gpu,cluster,storage]'
```

Do not force the CPU-tested PyTorch pin over the vLLM container's own dependency set. Run `pip check` after constructing the target image.

## CPU asynchronous benchmark

```sh
agent-replay rollout async-run --config examples/benchmarks/cpu-async.json --directory .replay/cpu-process
agent-replay rollout async-run --config examples/benchmarks/cpu-ray.json --directory .replay/cpu-ray
python tests/training_smoke.py
```

`async-run` writes `benchmark.json`, a snapshot and an online controller backup. Reusing its output directory resumes from the current committed policy and targets another configured number of updates. Do not compare a fresh and resumed run as identical experiments. The tiny offline transformer test creates its model/tokenizer locally, exercises two DDP ranks, verifies a correct-token probability increase, repeats the same batch without retraining, then restores optimizer/RNG state for the next update. It does not download a model.

## GPU GRPO run

1. Choose a small supported causal language model and a representative prompt/reward dataset. The bundled arithmetic prompt is a correctness smoke test, not an industry performance workload.
2. Place an actual local Hugging Face model/tokenizer checkpoint under `/checkpoints/initial` on the shared checkpoint volume. No remote Python code is trusted.
3. Run `agent-replay rollout seal-checkpoint /checkpoints/initial`. Insert the returned reference into `examples/benchmarks/gpu-grpo.json` (`initial_weights`). Never edit the files after sealing.
4. Copy/edit `examples/benchmarks/llm-config.json`. Set `ROLLOUT_LLM_CONFIG` to its absolute path in Actor and Learner environments. Keep temperature/top_p at 1 so stored behavior probabilities match the unwarped policy distribution used by the learner.
5. Run `agent-replay rollout async-run --config examples/benchmarks/gpu-grpo.json --directory .replay/gpu-run` in a Ray-capable environment. The profile reserves two one-GPU Actors and one two-GPU DDP Learner (four GPUs total). Use a model that fits each Actor GPU and each Learner replica.

A rollout job corresponds to one prompt with `group_size` completions. Its `generations` retain text, generated token IDs, behavior log probabilities and finish reasons; `prompt_token_ids` retain the precise input tokens. All completions for a prompt stay together. The independent verifier returns aggregate exact-answer reward; the learner independently reconstructs the per-completion reward vector and checks the aggregate. For a new reward function, implement a versioned trusted adapter rather than inserting untrusted executable reward code into payloads.

The bundled learner uses a single padded forward pass per rank and no gradient accumulation/FSDP. Scale the prompt-group batch and max sequence length to available memory. Truncated completions are retained with their `finish_reason` and included in this baseline objective. vLLM runs in an isolated child process for version correctness and GPU cleanup; warm persistent engines and NCCL weight transfer are follow-up optimization work, not claimed in these numbers.

## Asynchrony and policy freshness

`max_inflight` bounds unfinished Actor jobs plus unreserved trajectories within the accepted policy window. `max_policy_lag=0` is on-policy. A positive value allows a learner batch drawn from a single older behavior policy while training the latest base policy. The controller publishes only if that base policy is still active. The GRPO ratio uses captured behavior log probabilities; it is not silently reset to 1 for stale samples.

Only one learner job is scheduled at a time by the driver. SQLite transactions also protect against concurrent drivers, but production operation should use one driver per controller/workload. Exceeding the lag leaves old evidence stored, not recycled into a newer policy. Metrics distinguish produced rollouts from learner-consumed rollouts so speculative excess does not look like useful training throughput. Failed jobs stop the acceptance run; retained state can be inspected before a deliberate recovery.

## Storage and recovery

```sh
agent-replay rollout serve --tokens-file .replay/tokens.json --database .replay/controller.db --objects-root .replay/objects
# Or use standard AWS credentials/role and an existing bucket:
agent-replay rollout serve --tokens-file .replay/tokens.json --database .replay/controller.db --s3-bucket YOUR_BUCKET --s3-prefix experiment-a/
agent-replay rollout backup --database .replay/controller.db --output .replay/backup-001
agent-replay rollout restore --source .replay/backup-001 --database .replay/recovered.db --objects-root .replay/recovered-objects
```

The object backend configuration is persisted in controller settings; S3 credentials are never stored there. SHA verification occurs on every object/checkpoint read. Failed/uncommitted writes may leave unreferenced objects, which are safe to retain. Backup uses SQLite's online backup API, resolves external trajectory objects into a portable object directory and hashes the copied database. Restore requires a new path and invalidates previously leased capabilities. Role tokens and TLS keys remain separate operational secrets. Protect the backup because it contains private trajectories and job capabilities.

Training checkpoint publication is independent of controller publication. If the learner dies after finishing a checkpoint but before publishing its policy, retrying the exact policy/sample/settings key reuses that validated checkpoint. Parent checkpoint state restores optimizer/RNG before the next update. DDP saves rank-zero RNG; dropout is disabled and this single-node baseline assumes equal per-rank stochastic behavior. This is not general rank-specific RNG recovery for arbitrary stochastic distributed models. Checkpoint hashes are streamed to avoid loading entire tensor shards into memory.

## Kubernetes setup (templates, not applied)

Build/push the CPU and GPU images using `deploy/Dockerfile.cpu` and `deploy/Dockerfile.gpu`. Change image references in the YAML to your registry. Install KubeRay and the NVIDIA device plugin using their supported procedures.

Provide these existing resources in the target namespace:

- `rollout-checkpoints-rwx`: shared RWX durable checkpoint PVC mounted at the same absolute path on workers.
- `rollout-reports`: benchmark output PVC.
- `rollout-role-tokens`: Secret with `tokens.json` containing four distinct generated role credentials. Only controller/driver mount this file; workers receive only their own capability.
- `rollout-controller-tls`: Secret with `tls.crt` and `tls.key`; certificate valid for the controller service DNS name.
- `rollout-ca`: Secret with `ca.crt` trusted by client processes.
- `rollout-config`: ConfigMap containing `llm-config.json` and `cluster-benchmark.json`.

The cluster benchmark config derives from the GPU profile and adds:

```json
{
  "controller_url": "https://rollout-controller:8877",
  "tokens_file": "/secrets/tokens.json",
  "ray_address": "ray://rollout-ray-head-svc:10001"
}
```

Controller initialization and scheduling happen through the authenticated API. The remote driver cannot trigger local controller filesystem backup; schedule `rollout backup` on the controller volume separately. Ray worker nodes need the project and LLM configuration installed/mounted before scheduling. Templates expose ClusterIP services only; operate Ray on a private, trusted network and apply your cluster's namespace/network isolation policy. They do not provision accounts, GPUs, certificates or buckets.

## Metric definitions and benchmark protocol

| Requested metric | Measured definition |
| --- | --- |
| GPU utilization | Mean `nvidia-smi utilization.gpu` samples from reporting Actor/Learner devices, limited by `CUDA_VISIBLE_DEVICES` when set; not memory allocation, SM occupancy or achieved FLOPS |
| tokens/sec | Committed generated completion tokens / full run wall-clock seconds. Null for bandit runs; prompts excluded |
| rollout throughput | Committed prompt-group trajectory jobs / run seconds. Also report group size and consumed jobs; this is not individual completion count |
| learner idle time | Sum of reported learner waiting seconds and fraction of reported waiting+busy worker time |
| actor idle time | Same definition across Actor worker intervals; not divided by a single worker's wall time |
| end-to-end training step latency | Controller time from earliest enqueue among that learning batch's samples to committed policy publication; report count, mean, p50, p95, maximum |

Current reports include startup/model load and do **not** exclude warmup. CPU runs validate orchestration and measurement, not GPU/model throughput. Publish hardware, GPU count, exact model/checkpoint, precision, context/output length, group/batch sizes, worker counts, policy lag, dependency versions and commit. For a target-hardware study, run a separately labeled warmup, then at least three independent runs per configuration; compare median and spread. Keep the same workload and report sampling coverage, errors, produced/consumed samples and numerical quality/reward, not just throughput. Do not claim a win from one short run.

The controller serves authenticated Prometheus exposition at `GET /metrics` with the admin Bearer credential. `deploy/kubernetes/prometheus.yaml` gives a scrape configuration. Suggested charts: `rate(replay_generated_tokens_total[1m])`, `rate(replay_rollouts_total[1m])`, idle/busy `_sum` rates by role, and `histogram_quantile(0.95, sum by (le) (rate(replay_training_step_seconds_bucket[5m])))`. Measurements are persisted; metric IDs deduplicate network retries. The current implementation retains samples in SQLite without automatic downsampling; bound experiment duration and use an external Prometheus retention policy.

## Primary references

- [Ray GPU resources and scheduling](https://docs.ray.io/en/latest/cluster/kubernetes/user-guides/gpu.html)
- [KubeRay cluster configuration](https://docs.ray.io/en/latest/cluster/kubernetes/user-guides/config.html)
- [PyTorch distributed communication and DDP](https://docs.pytorch.org/docs/stable/distributed)
- [vLLM generation sampling parameters](https://docs.vllm.ai/en/latest/api/vllm/sampling_params/)
- [vLLM weight transfer](https://docs.vllm.ai/en/latest/training/weight_transfer/) — future optimized synchronization path, not implemented here
- [GRPO training and inference-policy mismatch](https://huggingface.co/docs/trl/grpo_trainer)
