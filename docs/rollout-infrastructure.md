# Distributed RL Rollout & Replay Infrastructure — v0.3 baseline

For the v0.4 extensions and current validation boundaries, see [distributed training](distributed-training.md).

This release implements a persistent, network-worker learning loop. The controller schedules rollout jobs pinned to immutable policies. Actors submit episode transitions; independently authorized verifiers attach rewards; learners consume reserved, policy-consistent batches and publish a new policy using compare-and-swap. Existing agent replay, structural diff and regression CI remain available at `/debugger.html`.

The included CPU demo trains a two-action softmax policy with actual REINFORCE gradients. It is a synthetic environment, not an LLM or GPU training demonstration. The included recorded acceptance run uses three Actor processes, two Verifier processes, 512 episodes and eight policy updates: optimal-action probability increases from 0.5 to 0.8340588334.

## Quickstart

```sh
python -m pip install -e '.[dev]'
agent-replay rollout demo --actors 3 --rounds 8 --directory .replay/rollout-demo
```

The demo starts a temporary loopback HTTP controller, launches workers as separate OS processes, waits for each round's verification, trains, then shuts down its own controller. Each invocation creates a separate SQLite database. `snapshot.json` can be imported into the hosted overview; `trace.json` can be imported into the existing debugger. No files or tokens are uploaded by importing a snapshot. The hosted Vercel site is an evidence viewer, not a running controller or trainer.

## Modules and boundaries

| Module | Responsibility |
| --- | --- |
| `rollout/store.py` | SQLite WAL policies, jobs, trajectories, transactional reservations, expiry fencing, immutable result receipts, active-policy CAS |
| `rollout/http.py` | Versioned HTTP API, role tokens, size limits, client requiring TLS for non-loopback URLs |
| `rollout/worker.py` | Claim, heartbeat, local trusted adapter execution, result submission and bounded retry |
| `rollout/bandit.py` | Seeded environment interaction, independent reward verifier, actual REINFORCE weight update |
| `rollout/cli.py` | Controller and worker commands, demo orchestration, snapshot and debugger export, bandit replay/diff |
| `dist/rollout.js` | Read-only snapshot overview, learning curve, worker states, policy lineage, import |
| `tests/test_rollout.py` | Races, expiry, stale writers, idempotency, provenance, batch isolation and HTTP role tests |

## Durable data model

- **Policy**: monotonically increasing `version`, SHA-256 digest of canonical JSON weights, immutable `weights`, parent version, source learning batch, timestamp. v0 is initialized once. The highest committed version is active.
- **Job**: stable idempotency key, kind (`actor`, `verifier`, `learner`), immutable payload, queued/leased/done/failed state, owner, random fencing token, deadline, attempt count (maximum three), result, request receipt hash. Claims and expiry processing use `BEGIN IMMEDIATE` transactions.
- **Rollout payload**: policy version and digest, environment version string, seed, rollout group and group size. Retrying the same group key preserves its original policy even after learning; changed request parameters conflict.
- **Trajectory**: immutable canonical episode JSON and digest, source job, policy, environment, seed. `rollout.v1` contains ordered transitions with observation, action, next observation, behavior log probability, terminated and truncated flags. The final transition must end the episode. Maximum 2,000 transitions. Reward and verifier version are attached separately by a Verifier, not trusted from an Actor.
- **Learning batch**: a learner job reserving an exact list of verified, previously unreserved trajectories with the current policy, environment and verifier version. No trajectory belongs to two batches. Publication commits weights and the job receipt in one transaction. If the active policy has advanced, the learner is rejected.

Weights and trajectories are currently JSON stored in SQLite, with integrity hashes checked by workers. This is not a tensor checkpoint/object-store implementation. Environment IDs and verifier IDs are version labels supplied by trusted adapters; they are not automatic code/environment hashes.

## Worker deployment

Create role credentials outside source control (the `.replay` directory is ignored):

```sh
mkdir -p .replay
agent-replay rollout tokens --output .replay/rollout-tokens.json
agent-replay rollout serve --database .replay/rollout.sqlite3 --tokens-file .replay/rollout-tokens.json
```

The tokens command uses exclusive creation and file mode 0600. Set `AGENT_ROLLOUT_TOKEN` separately in each process or your secret manager: admin for scheduling, actor for Actor workers, verifier for reward workers, learner for learning workers. Do not distribute the full credentials file to workers.

With the admin credential in the environment:

```sh
agent-replay rollout request initialize --json '{"weights":{"format":"bandit-softmax.v1","logits":[0,0]}}'
agent-replay rollout request enqueue --json '{"count":64,"environment":"two-arm-bandit.v1","seed":100,"key":"round-0"}'
```

Start any number of separately credentialed workers:

```sh
agent-replay rollout worker --role actor --owner actor-1
agent-replay rollout worker --role verifier --owner verifier-1
agent-replay rollout worker --role learner --owner learner-1
```

The default workers poll until stopped; `--drain` stops when no work is claimable. Once at least 64 episodes are verified, schedule the learning batch with the admin credential:

```sh
agent-replay rollout request batch --json '{"size":64,"environment":"two-arm-bandit.v1","verifier":"bandit-reward.v1","key":"update-0"}'
agent-replay rollout request policy
```

New rollout groups pin the latest policy. Workers load the pinned policy on each job; in-flight jobs never silently switch weights. Remote workers use `--url https://your-controller.example`; run the controller behind a TLS reverse proxy and private network. The controller can bind a selected interface using `--host`. Direct remote plaintext clients are rejected. Multi-host deployment has not been validated in the included local acceptance run.

## HTTP API

All routes use POST `/api/rollout/v1/{action}`, `Authorization: Bearer <role token>`, and a JSON object containing the named Store method arguments. Success is `{"result": ...}`; errors use `{"error": ...}` with 400 invalid input, 401 unauthorized, 403 wrong role, 409 lease/policy conflict. The request cap is 5 MB. Browser-origin requests and CORS are unsupported. No role token or lease token is included in snapshots.

| Action | Role | Main arguments |
| --- | --- | --- |
| initialize | admin | weights |
| enqueue | admin | count, environment, seed, key |
| batch | admin | size, environment, verifier, key |
| snapshot | admin | none |
| policy | all | optional version |
| trajectory | verifier, learner, admin | trajectory_id |
| claim | worker's own role, admin | kind, owner, optional seconds (1–3600) |
| heartbeat | workers, admin | job, token, optional seconds |
| fail | worker's own role, admin | job, token, kind, error, optional retryable |
| complete | actor, admin | job, token, trajectory |
| verify | verifier, admin | job, token, reward, verifier |
| publish | learner, admin | job, token, weights, metrics |

Completion, verification and publication accept identical retries using the successful lease token and receipt. Altered retries conflict. Lease expiry/reclaim fences all stale writes. Execution is at-least-once: adapters must make external side effects idempotent; fencing result commits does not undo external actions. Workers renew leases during adapter execution. Crashes recover on subsequent claims; adapter exceptions requeue up to three attempts. Failed or stale learning batches retain reserved samples for inspection; there is no automatic recycling that could train twice on ambiguous evidence. Operators currently start a new rollout group after an exhausted batch. Retry backoff, explicit batch abandonment and archival are follow-up work.

## Adapter interface

`worker --adapter your_installed_module` imports trusted local code only. It must implement:

```python
def rollout(payload, policy): ...  # returns rollout.v1 JSON

def verify(trajectory): ...       # returns (finite_reward, verifier_version)

def learn(policy, samples): ...   # returns (JSON_weights, finite_numeric_metrics)
```

The scheduler cannot send Python import paths or shell commands. Adapters are trusted, not sandboxed, and are responsible for environment safety, normalization/redaction, model execution and truthful behavior probabilities. Role-scoped credentials separate capabilities within one trusted deployment, not hostile multi-tenant actors. The bandit learner independently checks reward and behavior probability evidence.

## Replay and debugging

```sh
agent-replay rollout inspect --database .replay/rollout.sqlite3 --output .replay/snapshot.json
agent-replay rollout replay TRAJECTORY_ID --database .replay/rollout.sqlite3
agent-replay rollout replay TRAJECTORY_ID --database .replay/rollout.sqlite3 --policy-version 1
agent-replay rollout export TRAJECTORY_ID --database .replay/rollout.sqlite3 --output .replay/trajectory.trace.json
```

The replay command reproduces the included seeded bandit adapter and returns source/candidate trajectories, policy versions and an exact-content match. Another policy provides a counterfactual under the same environment seed. It does not claim deterministic replay of arbitrary GPU kernels or external environments. Exported v2 debugger traces retain transition and reward evidence; timestamps and timings are explicitly unavailable (epoch/zero placeholders), not measured. The export supports inspection/comparison; arbitrary rollout adapters are not automatically registered as regression rerun entrypoints. Existing registered-agent regression functionality is unchanged.

## Acceptance and remaining scale work

1. **Persistence and ownership**: exclusive concurrent claims, restart preservation, heartbeat, expiry, three-attempt exhaustion, stale completion rejection, duplicate completion receipts.
2. **Training correctness**: verifier gating, disjoint same-policy batches, no sample reuse, active-policy CAS, idempotent publication, seeded replay equality.
3. **Network protocol**: real loopback HTTP with independent role tokens, forbidden cross-role mutation, actual Actor → Verifier → Learner flow.
4. **Learning demonstration**: separate Actor/Verifier/Learner processes; 512 episodes, eight updates and improved optimal-action probability. CI reruns a smaller deterministic acceptance loop.
5. **Packaging and compatibility**: all previous regression/API/schema/JS tests, deterministic wheel/plugin/source sync, hosted overview and debugger navigation.

This is a functioning first infrastructure slice with network-distributed workers and one durable controller. It is not yet a production-scale distributed RL platform: no controller HA, PostgreSQL queue, object storage/tensor checkpoint transfer, GPU allocator, PPO/GRPO trainer, trajectory streaming, multi-tenant isolation, distributed optimizer, checkpoint retention policy or observability integration. SQLite must live on a controller-local filesystem; do not share its WAL database across hosts/NFS. Next scale milestones are object-store checkpoint references plus digest verification; a transactional Postgres scheduler; adapter capability routing; and an actual training-framework integration selected with the user.
