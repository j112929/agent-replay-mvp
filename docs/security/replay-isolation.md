# Replay isolation and compatibility change

The old runtime started a timeout-bounded process with the host environment and
registered Python entrypoint. Tool decorators only intercepted instrumented calls;
raw network/filesystem/subprocess code remained possible. A timeout and a process
group are not filesystem, network or credential isolation.

No secure isolation backend was present, so executable replay is **disabled**.
No container, restricted subprocess or microVM is claimed as implemented.

| Mode/kind | Implemented behavior |
| --- | --- |
| MOCK_REPLAY / TRACE_PLAYBACK | Copies sanitized recorded trajectory data, marks simulation; zero tool/model invocation |
| ToolInterceptor MOCK | Returns complete explicit recorded observations; missing/unknown/redacted observations reject |
| DENY | Reject |
| ALLOW_IN_SANDBOX | Reject: sandbox unavailable |
| SANDBOX_REPLAY / SANDBOX_EXECUTION | Reject; no fallback |
| MODEL_RERUN | Reject in replay; optional benchmark model runner remains explicit real execution |

READ_ONLY, STATE_MUTATING and UNKNOWN labels are advisory. None grants execution.
UNKNOWN requires an explicit complete recording to resolve a mock. No arbitrary
callback is accepted by the safe controller. Production endpoints and credentials
cannot be used because no replay networking or executable capability exists.
Fixed fixtures are data, not instructions to recover a secret. Recorded timing is
historical evidence; playback does not promise model determinism or task success.

```sh
agent-replay safe-replay trace.json --output .replay/safe-playback.json
# Explicitly rejected, exit 2:
agent-replay safe-replay trace.json --mode SANDBOX_REPLAY --output .replay/no.json
```

Intentional breaking changes:
- `replay --model`, `replay --tool`, `replay_agent`, `async_replay_agent`,
  runtime code execution and live/local replay policy rules reject before execution.
- `run`/`experiment` persist a failed ReplayDenied result instead of launching code.
- Regression cases requiring unavailable execution return inconclusive (exit 3).
- Regression cases may include a checksum-bound `candidate` trace reference. This
  evaluates newly captured evidence with the existing assertions; it is not rerun.
  Candidate identity must match the case and replay-generated evidence is rejected.
- Demo now explicitly captures bundled synthetic broken/fixed agents and evaluates
  those candidates; no payment/model service is involved.

A future sandbox backend must independently prove filesystem isolation, zero
production credentials, default-deny networking, quotas, deadlines, cleanup and
resistance to bypasses before ALLOW_IN_SANDBOX becomes available. This requirement
is not replaced by an application-level allowlist or a configuration flag.
