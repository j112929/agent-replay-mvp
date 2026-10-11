# Self-service workspace

Implemented incrementally on the existing guard slice. This is a runnable single
controller with connected signup, projects, team, tests, CI and test billing UI.
It has not been deployed as a hosted customer service or verified against a real
Stripe/SMTP account. Live billing is deliberately rejected.

## Run locally

Requires Linux, Node 24.15+ and Python 3.10+. No model API key is required.

```sh
python -m pip install -e '.[dev]'
npm ci
cp saas/.env.example .env
# Set BETTER_AUTH_SECRET to a generated random value, keeping it private.
npm run start:env
```

Open `http://127.0.0.1:3000/debugger.html`. Register with a synthetic email, create an
organization and a project. In local development verification is optional and
unconfigured mail stays in a bounded in-memory operator outbox; no public endpoint
exposes email tokens. For actual email verification, reset or invites, configure
SMTP. Without a persistent auth secret development sessions expire on restart;
accounts and artifacts remain intact.

The existing Python server and static Vercel site continue to load the legacy
debugger. The self-service UI activates only when the same origin advertises the
SaaS API. Vercel static hosting is not a persistent tenant backend. No hosting
migration, production deployment or live charge is included.

## Ten-minute synthetic demo

1. Open Failures → Load synthetic A/B demo. The bundled generic traces contain a
   synthetic token and a simulated patch operation; imports redact the token.
2. Choose Create test on failed Version B. Keep positive Version A and expected
   `{"output":{"fixed":true}}`. Generate and validate.
3. Open Regression tests → Review. Read the pair validation, declarative assertion
   and fixture provenance. As admin, approve the exact version.
4. Run test with baseline A and candidate B. The result is FAIL / regression, with
   zero model calls and zero real external tool operations. Inspect the report and
   recorded replay. A/A returns PASS. Missing evidence returns INCONCLUSIVE.
5. Create a suite to pin approved versions. A disabled required version still blocks
   CI as INCONCLUSIVE; disabling a test never silently passes its configured gate.
6. Create a scoped API key in Integrations, copy it once and store it in CI secrets.
   Use the generated Actions configuration with the bundled `saas/ci.mjs` helper.

The browser automation reproduces signup through billing and verifies mobile
overflow. The HTTP integration test additionally invokes the real remote CI helper
and verifies its process exit code. All test data is synthetic.

## Accounts and roles

Better Auth handles password hashing, sessions, cookie/CSRF protection, verification,
password resets and invitations. User passwords are never put into trajectory
storage. Production requires verified email and secure cookies. Password reset and
password changes revoke other sessions. Account settings can revoke other sessions.

| Role | Product permissions |
| --- | --- |
| Owner / Admin | Organization administration, all projects, billing, explicit test approval/rejection/control |
| Developer (`member`) | Assigned projects, trace import, case generation, test execution, scoped keys, project creation |
| Viewer | Read assigned project traces, cases, runs and reports; no mutations |

Invitation acceptance grants membership; admins separately grant project access.
Seat caps count pending invitations. Removing membership revokes keys and clears
project access. Project/key requests recheck membership and role every time. Key
rotation revokes the old key immediately. Keys expire in at most one year and
cannot generate/approve verifiers or manage billing/team settings.

## Stripe test integration

Configure a Stripe **test** secret key, endpoint signing secret and three recurring
monthly test price IDs for the Starter, Team and Business products.
Use active USD monthly prices matching `plans.json`; Checkout
rejects mismatched amounts, currency or recurrence. In Stripe's
test Billing Portal configuration, enable upgrades/downgrades, cancellation and
payment method management for these prices. The platform never handles card data.

Forward these events to `POST /api/billing/webhook`:

- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`

Checkout and Portal require an authenticated organization admin. Price IDs are
server configured. The webhook verifies the raw-body signature, rejects live
events, deduplicates processed event IDs and retrieves current subscription state
before reconciling entitlements. Serialized reconciliation handles reordered event
bodies. Retrievable active/trialing subscriptions with a known single-item price,
quantity one and a future billing period enable work. Past-due, unpaid, canceled,
invalid or expired state blocks new work. Existing evidence remains readable.

The tests inject a deterministic provider and use Stripe's actual signing/verifying
helpers; they make no payment calls. A real test Checkout, Portal, email delivery
and webhook reachability must be verified after operator configuration. Returning
from Checkout never activates a subscription. `sk_live_...` and live-mode events
are rejected; real commercial transactions require separate operator approval and
a reviewed implementation change.

## Usage, replay and processing

Plans and prices are centralized in `saas/plans.json`; an operator may override via
`GUARD_PLANS_FILE`. Imported traces, generated case versions and executions have
monthly organization caps. Projects, seats and physical artifact storage have
continuous caps. Model calls are zero; no paid generation or customer code runs.
Existing per-project guard artifact safety caps also apply; plan limits are upper
limits, not guaranteed throughput or a validated commercial service level.

Mutations reserve quota transactionally before starting the fixed Python worker.
Duplicate imports are refunded. Failures with no artifact writes are refunded;
partial/interrupted writes remain charged until operator reconciliation. One job
per organization and two per controller are permitted, with process/output/time
limits and process-group cleanup. SQLite stores small receipts, not duplicate trace
bodies. Idempotency lasts seven days or the latest 1,000 jobs per project. Completed
retries read the same immutable case/run artifact. No automatic expensive retries.

The Python worker accepts a fixed action allowlist and server-derived paths. Its
environment contains no auth/Stripe/SMTP secrets or home directory. It processes
JSON evidence, never shell commands, imports supplied code or calls live tools.
CPU/memory/file quotas protect trusted worker resource use; this is not a sandbox
for customer code. Executable replay remains disabled.

## API map

All custom APIs are `/api/saas/`. Browser mutations require same-origin JSON.
API integration requests use `Authorization: Bearer ...` and an `Idempotency-Key`
for imports, generation and evaluation. Never pass secrets as URL parameters.

| Path | Operations |
| --- | --- |
| `/me` | Account, memberships and pending invitations |
| `/organizations/:org/projects` | List/create scoped projects |
| `/organizations/:org/team`, `/usage`, `/billing`, `/audit` | Authorized organization views |
| `/organizations/:org/privacy` | Admin-configured extra structured redaction fields |
| `/organizations/:org/checkout`, `/portal` | Admin-only hosted test billing |
| `/projects/:project/import`, `/generate`, `/approve`, `/control`, `/evaluate` | Existing guard lifecycle; approval/control admin-only |
| `/projects/:project/traces`, `/cases`, `/runs` | List and inspect artifacts, recorded playback, HTML report |
| `/projects/:project/suites`, `/suites/:id/execute` | Pinned approved suites, evidence keyed by case ID |
| `/projects/:project/keys`, `/keys/:id/revoke`, `/keys/:id/rotate` | Scoped integration key management |
| `/projects/:project/access`, `/jobs` | Project authorization and bounded processing status |

Auth lifecycle and organization invitations use Better Auth's `/api/auth/` routes.
Only the documented account/org lifecycle endpoints are exposed; server-only and
future plugin routes are not implicitly public. Integrations → Redaction policy
lets admins configure additional structured field names for future imports;
mandatory credential redaction cannot be disabled. Existing evidence requires an
explicit migration, and approved fixtures are never silently rewritten.
Production account/organization deletion routes are disabled until the documented
complete retention/deletion procedure is implemented; do not delete only the auth
row and leave artifacts behind. Existing `/api/v1/` Python pilot APIs are unchanged.

## Verification

```sh
npm run test:saas
npx playwright install --with-deps chromium
npm run test:ui
python -m unittest discover -s tests -v
npm test
python scripts/package_release.py --check
```

The new Actions workflow runs these critical account/billing/evidence/browser tests
without provider keys. Real email/billing transactions and hosted infrastructure
are outside the offline verification claim.
