# Vast.ai four-GPU smoke test — 2026-11-10

## Result

**PASS:** a single-host vLLM → reward → GRPO smoke test completed two policy
updates on four RTX 4090 GPUs in Idaho, United States. Two GPUs were reserved
for rollout actors and two for the DDP learner.

This validates the bounded correctness acceptance checks for this configuration.
It does not establish reward improvement, model quality, representative throughput,
multi-host operation, controller high availability, or Kubernetes deployment.

## Configuration and provenance

| Item | Observed configuration |
| --- | --- |
| Date | 2026-11-10, approximately 16:34–16:41 Pacific |
| Provider / region | Vast.ai / Idaho, United States |
| GPU | 4 × NVIDIA GeForce RTX 4090 |
| Memory per GPU | 25,336,807,424 bytes |
| System RAM | Approximately 516 GB |
| Host interconnect | PCIe 4.0 x16, reported approximately 23.5 GB/s |
| Container image | `vllm/vllm-openai:v0.26.0` |
| Tested code | [`1f01df26a3f7175930610e0c07e111682c000739`](https://github.com/j112929/agent-replay-mvp/commit/1f01df26a3f7175930610e0c07e111682c000739) |
| Deployment branch | `deploy/vastai-gpu-smoke` |
| Model | `Qwen/Qwen2.5-0.5B-Instruct` |
| PyTorch | `2.11.0+cu130` |
| Transformers | `5.14.1` |
| vLLM | `0.26.0` |
| Ray | `2.58.0` |
| CUDA reported by PyTorch | `13.0` |
| Async run deadline | 300 seconds |
| Target | Two policy updates, two single-GPU rollout actors, two-GPU DDP learner |

The smoke script resolves the model revision and records it in the instance's
`provenance.json`. The exact resolved model SHA was not copied into this report.
The tested code includes live driver-log streaming and a read-only controller
diagnostic utility.

## Observed evidence

Dependency integrity (`pip check`), four-GPU CUDA preflight, model download,
and local Ray initialization passed. Final `acceptance.json` was observed in
the container logs:

```json
{
  "passed": true,
  "gpu_count": 4,
  "updates": 2,
  "ddp_world_size": 2,
  "generated_tokens": 76,
  "note": "Smoke acceptance only; does not establish training improvement"
}
```

The smoke acceptance checks require committed policy versions, generated tokens,
learner consumption of rollouts, sealed training checkpoints, finite loss, and
the expected learner world size. Uniform rewards can produce zero GRPO
advantages; passing does not demonstrate learning gains.

This report transcribes observed console evidence. The full controller database,
checkpoints, provenance file, and raw logs remain on the stopped instance and
have not been exported into this repository. No timing or utilization number is
presented as a benchmark.

## Reproduction

Use a fresh run directory on a compatible four-GPU host. The deployment scripts
are on the pinned deployment commit, not necessarily the default branch.

```sh
git clone --branch deploy/vastai-gpu-smoke --single-branch https://github.com/j112929/agent-replay-mvp.git
cd agent-replay-mvp
git checkout --detach 1f01df26a3f7175930610e0c07e111682c000739

# Repairs required by the tested base-image state.
apt-get update
apt-get install -y libcairo2-dev pkg-config python3-dev
python3 -m pip install --no-cache-dir pycairo nixl-cu13==1.3.1

bash deploy/vastai/bootstrap.sh \
  --directory /workspace/replay-data/smoke-001 \
  --gpus 4 --timeout-seconds 300
```

The base image originally lacked `pycairo` for `pygobject` and paired
`nixl==1.3.1` with `nixl-cu13==1.3.2`. The repairs above passed dependency
checks on the tested hosts. Dependencies installed by bootstrap are not a
complete frozen lockfile, so a later run can resolve different package versions.

Inspect `run.log`, `hardware.json`, `provenance.json`, `run/benchmark.json`,
`checkpoints/`, and `acceptance.json` under the run directory. For retained
controller state:

```sh
python3 deploy/vastai/diagnose.py \
  /workspace/replay-data/smoke-001/run/controller.sqlite3
```

The diagnostic summarizes retained task state and exception classes. Its last
unfinished stage is a state summary, not proof of the underlying root cause.

See the [deployment guide at the tested commit](https://github.com/j112929/agent-replay-mvp/blob/1f01df26a3f7175930610e0c07e111682c000739/deploy/vastai/README.md).

## Earlier attempts and unresolved findings

| Attempt | Outcome |
| --- | --- |
| Initial host | Image extraction failed with host disk “no space left on device”; instance destroyed. |
| Earlier California RTX 4090 host | Bootstrap stopped on base-image dependency conflicts before the repairs above. |
| Lithuania, 4 × RTX A5000 | Dependency checks, CUDA preflight, model download and Ray initialization passed; the async run reached its 1,800-second deadline. Final acceptance was false. |
| Idaho, 4 × RTX 4090 | Two updates and two-GPU DDP smoke acceptance passed with a 300-second deadline. |

The earlier A5000 output included:

```json
{"error": "Asynchronous run deadline exceeded; database retained for recovery"}
```

```json
{"passed": false, "error": "GPU driver exited with status 2; inspect run.log"}
```

Direct and provider-proxy SSH attempts to earlier instances were refused before
authentication. The available evidence does not distinguish the execution
environment's network restrictions from host forwarding/proxy issues. Temporary
SSH access used for those attempts was revoked.

The A5000 timeout's root cause remains unconfirmed. Success on a different host
with updated diagnostics and a different deadline does not isolate a causal fix;
there is insufficient evidence to attribute the failure to NCCL, P2P, or
application scheduling.

## Cost and resource lifecycle

At the test-time quote, the Idaho instance cost **$2.723/hour plus bandwidth**.
The observed account credit change during this replacement/test window was
approximately **$0.24**; this is not an itemized invoice and may be affected by
settlement timing and retained storage on other instances.

The Idaho GPU instance was stopped after acceptance passed. Its retained 100 GB
container disk was quoted at **$0.056/hour**, approximately **$1.34/day**, while
stopped. At the final observation, all three retained instances were stopped;
their aggregate disk charges were approximately **$0.196/hour** or **$4.70/day**.

These are historical quotes, not current offers. Stopping a process does not
stop provider GPU billing. Stop the instance after collecting results; export
artifacts before destroying it. Destroying an instance removes its unexported
container-local data.
