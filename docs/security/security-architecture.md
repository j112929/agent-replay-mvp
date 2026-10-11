# Security architecture — pilot controls, not certification

The product remains an Agent Benchmark, Evaluation, Replay & Regression Platform.
These controls harden its existing Python SDK, file artifacts, debugger, benchmark
comparison and GitHub Actions workflow. No SOC 2, penetration-test, container or
microVM security certification is claimed.

## Implemented boundaries

- `security/sanitizer.py`: STANDARD/STRICT/CUSTOM structured and text detection,
  encoded JSON handling, optional PII, conservative malformed/cyclic/converter
  failure handling. Required credential keys cannot be disabled by CUSTOM.
- Capture spans, tool/model observations, exceptions, journals, atomic artifact
  storage, trace imports/exports and server responses apply sanitization. Rollout
  object writes and committed trajectory/error payloads are sanitized too.
- Additive `privacy` metadata records sanitized paths, methods, classification,
  unavailable observations and replay limits. Placeholders contain no original
  secret or secret hash. This is detection, not proof a trajectory contains no PII.
- Safe replay accepts data only. No executable agent/tool capability is present.
  `MOCK_REPLAY / TRACE_PLAYBACK` works; `SANDBOX_REPLAY`, `MODEL_RERUN` and
  `SANDBOX_EXECUTION` reject. See [replay isolation](replay-isolation.md).
- Regression assertions reuse the existing runner and comparison engine. An
  explicitly supplied candidate trace is checksum-checked and identified;
  playback cannot masquerade as a fresh candidate execution.
- Release gates extend the existing benchmark namespace through `--policy`,
  producing PASS/FAIL/INCONCLUSIVE. Default production inconclusive handling blocks.
- Optional organization mode on the existing HTTP server resolves a principal
  from a server-configured SHA-256 bearer digest and enforces tenant roots/RBAC.
  Unauthenticated local desktop mode remains single-user and loopback-only.

## Pilot identity configuration

Use high-entropy randomly generated tokens of at least 32 characters. Generate
these outside the capture system, deliver securely, and put only digests in an
owner-readable configuration file. Example shape (replace placeholder):

```json
{
  "retention_days": 30,
  "identities": [
    {"token_sha256": "64 lowercase hex characters", "org_id": "acme", "role": "ADMIN", "subject": "operator-1"}
  ]
}
```

```sh
agent-replay serve --directory .replay/pilot/traces --access-config /secure/access.json
```

ADMIN can read, import, compare, request playback, change release policies,
accept baselines and run retention. DEVELOPER can read/import/compare/playback;
VIEWER only reads/exports. Organization IDs in request bodies do not select storage.
API resources live below `.replay/pilot/organizations/<server-selected-org>/`;
audit records live separately below `.replay/pilot/audit/`.

The existing loopback host/origin checks remain. This is an authenticated local
pilot API, not a ready-to-expose Internet service. Remote deployment requires TLS,
a reviewed gateway, identity lifecycle/rotation, rate limits and an independent
security review. No SSO/OIDC, invitation system or user self-service is implemented.
Static UI assets are public, API data is authenticated. Tokens stay in page memory;
UI evidence no longer uses localStorage. Existing browser caches are not read or
automatically erased; owners must remove historical sensitive caches explicitly.

Policy changes use ADMIN-only `POST /api/v1/policies` with a `release` policy object.
GET returns current policy. CLI gates take an explicit policy file. Organization `POST /api/v1/benchmark-gate`
accepts baseline/candidate run IDs, loads the ADMIN-managed policy server-side and
persists the decision in that organization's reports. Client-supplied policies or
organization IDs cannot select another tenant. There is no deployment orchestrator
implicitly trusting a browser-selected rule.
Audit logs record access attempts, exports, replay requests, policy changes and
retention actions without bodies, credentials or raw errors. They are local
append-only application logs, not tamper-proof storage against an OS administrator.
Direct filesystem access and offline CLI operations are trusted operator actions
outside HTTP RBAC/audit. Rollout training services remain separately administered.

## Storage and migration

No new database, PostgreSQL or RLS layer is introduced. Keep v1/v2 schemas, adding
metadata fields. Existing data is not silently assigned a tenant. Export/import
through the sanitizer into the intended organization; preserve pre-migration backups
under your separate retention/access policy. Historical plaintext on disk is not
retroactively erased. The source archive and plugin are synchronized by the existing
packaging script.

## Remaining limitations

Secret detection cannot identify arbitrary opaque values without semantic context.
Configure sensitive field names, avoid recording unnecessary data, and use STRICT
when known PII should also be masked. Custom regex rules are trusted administrator
configuration, not an untrusted public input. They are not a ReDoS-resistant engine.
The SDK controls captured/logged artifacts, not arbitrary application stdout or
third-party logging. External services, backups and screenshots are outside its
purview. No secrets are reconstructed; incomplete evidence is surfaced to the gate.
