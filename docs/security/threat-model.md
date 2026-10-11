# Threat model

| Threat | Enforced control | Residual risk |
| --- | --- | --- |
| Credentials in nested tool arguments/responses | Required field rules, recursive JSON/text sanitizer before sinks | Unknown opaque secrets require configured fields; no universal detector |
| Broken custom converter leaks payload | Fixed failure marker, safe warning, no fallback to raw object | Caller may independently log its application data |
| Email/payment/database/cloud operation during replay | Data-only capability boundary; callable/live replay entries deny before execution | Normal capture/benchmark/training is real execution, not sandboxed replay |
| Uninstrumented Python bypasses decorators | Executable reruns disabled; no subprocess launched | Secure sandbox backend not implemented |
| Redacted values treated as usable observations | Envelope downgrade, mock refusal and explicit replay limitations | Playback can display placeholders, not reproduce hidden data |
| Forged organization ID | Server resolves organization from configured token digest | OS owner and trusted offline operators can access artifacts |
| Viewer invokes mutation | Server-side role check before route dispatch | Token theft grants configured role until operator rotates it |
| Cross-tenant trace/job/report ID | Per-principal Store/project roots; path containment | Local administrators, writable symlinks and filesystem tampering are outside boundary |
| Quiet regression hidden by mean success improvement | Matched-task regressions, critical tags and structural diff | Single observations cannot estimate model nondeterminism reliably |
| Statistical noise | Minimum samples, exact paired sign/binomial test, explicit inconclusive state | Dataset selection bias, dependence and repeated trials need study design |
| Missing metrics/evidence interpreted as pass | Production gate blocks inconclusive | DEVELOPMENT policy may explicitly allow, and this is reported |
| Incomplete deletion | Whole inactive tenant cohort deletion includes journals/derived artifacts | Backups, audit and external indexes require separate operator policy |

Trust boundaries: production agent process; sanitized artifact storage; local HTTP
service; token principal/tenant namespace; CI policy evaluator. Captured traces and
browser requests are data, never code/configuration authority. User-provided local
benchmark verifiers and normal agent code remain explicitly trusted execution.

This implementation is a pilot foundation. It does not provide an adversarial
code sandbox, remote multi-user hosting stack, tamper-resistant audit, key management
service, enterprise identity federation, or independently verified compliance.
