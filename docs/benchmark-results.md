# Distributed RL CPU benchmark

Three independent cold-start runs per executor; 3 Actors, 1 Verifier, 1 Learner, four updates, 16 jobs per learning batch. Synthetic CPU bandit workload, not GPU/LLM throughput.

| Metric | Process workers | Ray workers |
| --- | ---: | ---: |
| Rollout throughput median / s | 104.41 | 35.80 |
| Training step p95 median / s | 0.431 | 0.439 |
| Actor idle fraction median | 81.1% | 85.1% |
| Learner idle fraction median | 96.2% | 95.1% |
| GPU utilization | Not measured | Not measured |
| LLM completion tokens/sec | Not measured | Not measured |

Every run consumed 64 rollout jobs in the learner. Produced jobs include speculative excess; raw reports retain both counts. Latency is enqueue-to-policy-publication on the controller clock. Idle fractions cover reported worker intervals and exclude unreported partial intervals. Ray startup is included, so these short runs are not a steady-state framework speed comparison.

DDP acceptance: two CPU/Gloo ranks, actual GRPO gradients on a locally initialized tiny GPT-2 model; correct-token probability 0.160318 → 0.203936. Checkpoint retry reuse and optimizer-state restoration passed.

GPU/vLLM/CUDA/NCCL and Kubernetes execution were not run: no GPU or cluster was provided.

Software: Python 3.12.14, PyTorch 2.14.0, Ray 2.58.0, Transformers 5.17.0. Full per-run metrics and source digests are in the accompanying JSON.
