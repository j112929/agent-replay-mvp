# Commercial security gaps

The original repository assessment is [architecture/security-gap-analysis.md](../architecture/security-gap-analysis.md).
The commercial assessment is [productization/current-state-assessment.md](../productization/current-state-assessment.md).

Reuse required credential sanitization, data-only replay, tenant roots, ADMIN /
DEVELOPER / VIEWER checks and scoped local audit from PR #3. New imports must reject
failed sanitization before writing anything and treat OTLP attribute pairs as
structured keys, not only strings. No raw import payloads enter errors/logs.

Generated rules/fixtures require positive and negative evidence, provenance and
digest-bound approval; arbitrary generated code is prohibited. Candidate playback
must not be accepted as fresh agent execution. Incomplete critical evidence blocks
the gate. Quotas must be checked server-side and before writes.

Hosted gaps: self-service accounts, project membership, revocable scoped keys,
transactional entitlements, TLS termination for a public API, managed encryption
at rest, per-record retention, backup deletion guarantees, tamper-resistant audit,
error monitoring and restore drill. Vercel serves static assets only. Nothing here
claims SOC 2, complete regulatory compliance, secure arbitrary-code isolation, or
live commercial billing. Do not expose the loopback pilot via a public proxy as a
substitute for those controls.
