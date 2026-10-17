**Trusted candidate release evidence**

An ordinary `guard run`/`POST evaluate` evaluates captured records. A business PASS
now explicitly has `release_eligible: false`. For release protection, use
`guard release`/`POST release` and require both `decision == PASS` and
`release_eligible == true`. Missing/invalid evidence exits 3 (INCONCLUSIVE), a
business or cost failure exits 1, and an eligible pass exits 0.

This adds a trust boundary around the existing evaluator, not a new execution
backend. The controller never imports or executes customer Agent code. Unisolated
executable replay remains disabled. Capture runs only in your own trusted CI.

**What the release binds.** An administrator issues a single-use challenge before
execution. It fixes the exact approved case/version and assertions, recorded
fixtures, baseline digest, full Git commit, candidate version, model/prompt/tool/
environment configuration digest, trusted runner identity, expiry and USD budget.
The candidate capture records that challenge and CI run ID. The signer checks a
clean checkout and matching captured provenance, freshness and capture health,
then signs the manifest with an operator-controlled 32-byte HMAC key. The manifest
binds the complete normalized execution record (excluding derived `privacy`
display annotations), configuration, run and measured cost.

Verification authenticates the trusted signer and checks all bindings against
controller-owned state. Invalid signatures do not consume the challenge.
Authentic evidence is consumed once even when the business assertion or budget
fails. Concurrent submissions cannot both pass. A crash after consumption requires
a fresh challenge and capture; it cannot silently retry old evidence.
An identical HTTP Idempotency-Key may retrieve its original immutable receipt;
a new submission of that same proof is rejected.

HMAC is **not** proof that arbitrary code was executed honestly. A compromised
runner or stolen signing key can lie. Protect the signing job/environment, keep
keys out of pull-request jobs from untrusted forks, and scope a different key to
each controller project. Do not give a model, browser, trace importer or customer
Agent process the key. Release verification needs the original baseline/case
artifacts, not just the exported proof. Key rotation revokes unconsumed proofs
signed with the old key. There is no automatic OIDC provider attestation here.

**Local/controller CLI.** Use a separate private controller directory and an
ignored `.replay/` output directory or a path outside the candidate checkout.
The current case must already be generated, validated on positive/negative
examples and approved. These commands do not approve cases implicitly.

```sh
# configuration.json contains exactly these four explicit version identifiers:
# {"model":"pinned-model","prompt_version":"prompt.v1",
#  "tools_version":"tools.v1","environment_version":"environment.v1"}
agent-replay guard challenge CASE_ID --directory CONTROLLER \
  --baseline BASELINE_ID --commit FULL_40_CHARACTER_SHA \
  --candidate-version CANDIDATE_VERSION --configuration configuration.json \
  --runner trusted-ci --max-cost-usd 0.10 --output challenge.json
```

The trusted customer-owned runner captures its actual code after receiving the
challenge. Add the metadata to your existing explicit Capture instrumentation:

```python
from agent_replay import Capture
from agent_replay.guard.evidence import capture_context
from agent_replay.storage import read

challenge = read("challenge.json")
configuration = read("configuration.json")
metadata = {"agent_id": "your-agent", **capture_context(challenge, "CI_RUN_ID", configuration)}
with Capture("release candidate", ".replay/candidate/traces", input=task,
             metadata=metadata, durability="sync", on_capture_error="raise") as capture:
    capture.set_output(your_trusted_agent(capture))
```

The signer checks the Git checkout again. The trusted CI job must supply the
configuration actually used and a complete measured USD cost; a configuration
string or cost number is not independently inferred from the model API. An
unpriced cost must not be guessed as zero. Zero is valid for an explicitly free
synthetic workload. The file/environment name below is a secret input, never a
command-line value:

```sh
# Privately provision GUARD_EVIDENCE_SIGNING_KEY as 64 lowercase hexadecimal chars.
agent-replay guard attest --challenge challenge.json --trace CANDIDATE_TRACE.json \
  --configuration configuration.json --repository CLEAN_CHECKOUT \
  --run-id CI_RUN_ID --cost-usd MEASURED_USD --output evidence.json
agent-replay guard import CANDIDATE_TRACE.json --directory CONTROLLER
agent-replay guard release CASE_ID --directory CONTROLLER \
  --baseline BASELINE_ID --candidate CANDIDATE_TRACE_ID \
  --evidence evidence.json --output release-gate.json
```

Configuration changes, dirty checkouts, changed case/fixture/baseline, playback,
stale captures, missing cost, wrong signing keys, expired/reused challenges and
tampered execution records cannot become an eligible PASS. The default challenge
lifetime is 15 minutes (30–3600 seconds configurable). The local controller is
single-operator trusted filesystem state, not protection against its root user.

**SaaS API.** This uses the existing authenticated project API, quotas, audit log
and immutable reports. The production worker requires Linux resource limits.
No hosted deployment or live Agent execution is introduced by this change.

Privately create an operator-owned JSON file **outside the repository, artifact
root and static web root**, restrict its permissions, and set
`GUARD_EVIDENCE_KEYS_FILE` on the controller. Its shape is:

```json
{"ORG_ID/PROJECT_ID":{"runner_id":"trusted-ci","secret":"64-lowercase-hex-characters"}}
```

Use real random values, never that placeholder. Each project gets a different
secret; provision only that project's secret as `GUARD_EVIDENCE_SIGNING_KEY` in
its protected CI signing job. The file is read by the fixed Python worker only
for release operations, not accepted from request bodies or persisted as evidence.

1. An administrator account session calls
   `POST /api/saas/projects/PROJECT_ID/release/challenges` with `case_id`,
   `baseline`, `commit`, `candidate_version`, `configuration`, `max_cost_usd`
   and optional `version`/`ttl_seconds`. API keys cannot choose release targets
   or approve tests. The runner identity comes from operator configuration.
2. Transfer that challenge into the protected CI job, capture and attest as above.
3. The existing CI key needs import/run permissions. Submit via:

```sh
node saas/ci.mjs --server https://YOUR_CONTROLLER --project PROJECT_ID \
  --case CASE_ID --baseline baseline.json --candidate CANDIDATE_TRACE.json \
  --release-evidence evidence.json --output release-result.json
```

The client uploads sanitized baseline/candidate evidence and calls the strict
`POST release` endpoint. Without `--release-evidence` it retains its prior
record-evaluation behavior; that result is explicitly not release eligible.
For direct API clients, the strict request contains `case_id`, `baseline`,
`candidate`, optional `version` and the signed `evidence` bundle. Reuse the same
Idempotency-Key only to retrieve the original request's result.

**Verification.** `tests/test_release_evidence.py` executes fresh toy captures in
temporary clean Git checkouts, exercises both CLI exit codes and all rejection
paths, and checks concurrent consumption. `tests/saas/release.test.mjs` runs the
real HTTP/remote-CI flow on Linux, including administrator-only challenges,
project-specific signing trust, repeat submissions, idempotency and a cost failure.
`scripts/release_evidence_smoke.py .replay/release-smoke` demonstrates fresh
capture → signature → PASS/FAIL/INCONCLUSIVE with a throwaway synthetic signer.
The workflow validates this mechanism, not production reliability or RL quality.
