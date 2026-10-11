# Self-service accounts, billing and product workspace

Assessment: 2026-10-11, based on the tested guard PR #4. Capture, import,
declarative verifier validation, approval, recorded replay, regression reports and
three-state CI gates are reusable. Authentication is operator-configured pilot
access; signup, invitations, subscriptions and a connected product UI are missing.

## Incremental decision

Add one Node 24 controller with Better Auth and native SQLite, next to the existing
Python SDK and vanilla frontend. Do not put account data into the rollout database.
The controller owns sessions, organization membership, project access, scoped API
keys, usage reservations and Stripe test billing. It invokes a fixed, bounded
Python JSON worker for existing guard operations. No customer code or tool callback
is accepted. Retain the legacy local server and static Vercel site.

SQLite and file artifacts require one controller with durable disk. This is not a
multi-instance or stateless Vercel Functions deployment. A future hosted migration
requires a managed database and durable artifact backend before scaling out.

## File-level plan and dependencies

| Files | Responsibility | Dependency |
| --- | --- | --- |
| `saas/config.mjs`, `db.mjs`, `auth.mjs` | Validated configuration, migrations, established auth and org plugin | Node 24, Better Auth |
| `saas/server.mjs`, `domain.mjs` | Organization/project authorization, invites, keys, usage, jobs and audit | Auth and database |
| `saas/billing.mjs`, `plans.json` | Test Checkout/Portal, verified webhook ledger, entitlements | Organization authorization, Stripe |
| `saas/worker.mjs`, `sdk/agent_replay/saas_worker.py` | Data-only bridge to guard | Existing import/store/runner |
| `dist/workspace-loader.js`, `guard-ui.js`, `guard-ui.css` | Signup, onboarding and seven connected workspace views | Controller API |
| `tests/saas/`, CI workflow | Auth, tenancy, billing, quotas and end-to-end validation | All preceding modules |
| docs, README, packaging | Setup, migration/rollback, deployment limits | Verified implementation |

## Prioritized checklist

- [x] SQLite migrations and account/session lifecycle.
- [x] Organization invitations, role checks, projects and scoped keys.
- [x] Server-enforced plan entitlements, bounded jobs and transactional reservations.
- [x] Test-only hosted billing code, signed and deduplicated webhook tests.
- [x] Connected product screens with clear configuration and validation states.
- [x] Offline synthetic signup → import → approved case → regression gate demo.
- [x] Existing tests, new critical security tests and UI browser checks.

Actual results and external configuration limits:
[self-service-verification.md](self-service-verification.md).

## Migration and rollback

New account tables and Better Auth tables are additive; existing JSON schemas and
Python APIs remain unchanged. Start explicitly initializes migrations. Back up the
account database and artifact directory together before upgrades. Stop the single
controller before backup/restore. Rolling back means retaining the new database
and returning the UI loader to legacy mode; account data is not converted into
legacy identities. No existing tenant artifacts are silently assigned to users.

## Security and test strategy

Passwords/sessions belong to Better Auth, not custom JWT/password code. All project
requests derive tenant identity from a verified session or hashed, scoped API key.
Keys do not bypass current membership checks. Authoritative billing comes from
signed webhooks and provider retrieval, never redirects. Live keys and live events
are rejected in this milestone. Production startup requires HTTPS, a strong secret
and SMTP for verified email; local-only development can use an in-memory mail outbox.

Test actual HTTP signup/session/CSRF, organization and project boundaries, role
changes, key rotation/revocation, quotas, bounded workers, webhook signatures,
duplicates, reordered events, inactive subscriptions and live-mode rejection.
Billing uses a deterministic test provider when no Stripe account is configured;
this verifies integration code, not a real Checkout transaction. Browser-check the
connected UI and rerun the existing SDK and web suites.

## Operational complexity and remaining risks

One controller, SQLite backups, SMTP and hosted Stripe test endpoints are the new
operational surface. No queue or paid model execution is added. Founder support and
pricing remain hypotheses; no claim of commercial readiness or product-market fit.
Production hosting, real provider credentials, backup restore drills and email
deliverability must be verified by the operator before external customer rollout.
