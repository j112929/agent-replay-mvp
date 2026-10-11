# Self-service controller runbook

## Hosting boundary

Use one Linux Node 24 controller and Python 3.10+ with a persistent local disk.
SQLite accounts and sanitized artifact roots are in `GUARD_DATA_DIR`. A process
lock refuses a second controller and handles dead local process locks. Shared
network volumes, multiple hosts, Vercel Functions and ephemeral filesystem storage
are unsupported. Existing Vercel static deployment stays unchanged.

Production startup requires `NODE_ENV=production`, exact HTTPS `GUARD_ORIGIN`, a
random `BETTER_AUTH_SECRET` of at least 48 characters, authenticated SMTP URL and
mail-from address. Terminate TLS at an operator-controlled proxy preserving the
configured Host. Arbitrary forwarded-host/origin headers are not trusted. Configure
disk encryption, backups, firewall and proxy rate limits on the chosen host before
exposing it. Neither this code nor SQLite alone provides encryption at rest.

## Migration and rollback

First start runs Better Auth migrations and additive application schema v1. It does
not migrate the rollout database, existing pilot identities or unowned file roots.
Back up before dependency upgrades; an unsafe auth schema change must fail startup.
Release this controller independently of the existing local SDK. Rollback to the
prior SDK/static UI leaves new account data untouched; do not drop or reassign it.
Do not disable sanitizer/approval safeguards to recover from a schema mismatch.

## Backup and restore

1. Stop the controller and confirm no fixed worker is running.
2. Copy the whole data directory, including SQLite/WAL/SHM and sanitized artifacts,
   into an access-restricted encrypted backup. Keep auth/SMTP/Stripe configuration
   in the operator secret manager; do not publish it with source or reports.
3. Restore into a separate restricted directory; remove the copied `controller.lock`
   only after confirming its old process is absent. Use the same auth secret if
   sessions must remain valid. Stripe test IDs remain bound to their original orgs.
4. Start in isolation, verify account/project membership, immutable approval hashes,
   an A/A PASS and an A/B FAIL, and physical usage before admitting traffic.

The account tests verify restart and additive migration preservation. Cloud restore,
encryption and external backup scheduling are operator responsibilities and have
not been validated by this implementation.

## Health, limits and incidents

`GET /api/health` exposes bounded readiness information. Logs contain request ID,
method, status and elapsed time only, not paths, payloads, headers or auth links.
Authenticated admins can inspect security audit events; those contain IDs and
operations, never trace bodies. Audit retains the latest 10,000 records per org.
The signed billing ledger retains processed events for 90 days. Usage is monthly;
job receipts retain seven days / latest 1,000 per project. Sessions/auth token
expiry is managed by Better Auth; operator maintenance of long-lived account data
and metadata growth remains necessary. No external error-monitor/alert service is
silently installed.

On 429: inspect usage, project safety caps, storage and pending invitations. On
INCONCLUSIVE: inspect required positive/negative evidence, identity, recorded
fixtures and exact approval. On worker timeout: the process group is killed; a
partial write stays charged, so inspect artifacts before reconciliation. Never
retry with a production tool or provider credential. Jobs interrupted by controller
restart are marked interrupted; reservations remain conservatively charged.

For billing retries inspect minimal event IDs/status and configured price IDs;
reconciliation retrieves authoritative test subscription state. Never edit a plan
to paid because a browser redirect says success. Invalid/unreachable webhook or
provider state cannot silently grant paid access. Verify forwarding health and test
payment failure/cancellation before any partner deployment.

## Retention and deletion limitation

File cohorts, account database, approval/suite references and usage/audit records
must be considered together. A complete self-service organization deletion workflow
is not implemented; destructive account/org endpoints are disabled. Operator-led
deletion requires stopping writes, canceling the test subscription, deleting scoped
artifacts and derived references, revoking keys/sessions, and separately applying
agreed audit/backup/CI export retention. Do not promise immediate deletion from
backups, browser downloads or GitHub artifacts. No regulatory certification is
claimed.

## Lightweight cost model

Recorded evaluation has zero provider-model cost. Let `N` be jobs/month, `T` average
worker seconds, `S` artifact GB, and `B` backup GB. CPU demand is approximately
`N*T/3600` worker-hours, bounded by two workers; disk demand is `S+B` plus account,
usage and bounded job/audit metadata. SMTP costs depend on verified account/invite
volume. Hosted test billing makes no real charge; live processor fees are unmeasured.

A single modest VM/durable volume and SMTP plan are an operational starting point,
not a vendor quote. Track actual p95 worker duration, disk/DB growth, rejected
quotas, backup age and operator support minutes before promising the target 5–10
maintenance hours/week. No guaranteed ARR or marginal cost is inferred from tests.
