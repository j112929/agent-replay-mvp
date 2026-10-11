# Self-service implementation verification

Validated in the repository working tree on 2026-10-11/12 with Node 24.19.0 and
Python 3.12. The installed editable SDK was explicitly switched to this repository
before compatibility tests, and the fixed worker uses this repository's SDK path.

| Check | Actual result |
| --- | --- |
| Existing Python SDK/benchmark/replay/guard/rollout/security suite | 117 passed |
| Existing Node trajectory/debugger tests | 10 passed |
| New self-service HTTP/security/billing/workflow/operations tests | 12 passed |
| Browser onboarding and seven connected views; viewer permissions/XSS | 2 passed |
| Wheel build | succeeded |
| Runtime dependency audit | 0 reported vulnerabilities |

HTTP evidence includes real signup/session/password hashing, private verification
and reset links, revoked sessions, org/project isolation, role updates, scoped
hashed keys and rotation, invitation seat caps, trial expiry, transactional quotas,
request bounds, idempotency and controller restart preservation. Custom structured
redaction cannot disable credential protection.

Billing tests use a deterministic test provider and Stripe SDK signature helpers.
They cover Checkout/Portal permissions, redirects not granting entitlements,
signature rejection, processed-event duplicates, reordered event bodies, live event
rejection, foreign subscription identity and unknown price fail-closed behavior.
No Stripe API transaction or real SMTP delivery occurred.

The synthetic workflow imports sanitized A/B evidence, generates a verifier that
discriminates the example pair, binds explicit approval, produces PASS/FAIL and
INCONCLUSIVE, persists reports/suites and invokes the dependency-free remote CI
client. B exits 1, summaries contain no synthetic token or API key, recorded playback
has zero external operations/model calls, and disabled/rejected versions cannot
bypass release protection.

The browser tests exercise actual cookie sessions, forms and APIs, render desktop
and 390px mobile screenshots, assert no horizontal document overflow, check all
seven product screens, create a suite/key, and verify a viewer cannot mutate or
execute imported markup. The default Playwright CDN download was unavailable in
this environment; local browser verification used an independently extracted
Chromium headless binary. CI uses Playwright's standard browser installation.

## Explicit limitations

- Code and offline integration are verified; the hosted SaaS is not deployed.
- Operator SMTP delivery, Stripe test account flow and webhook reachability remain
  unverified. Live keys/events are disabled; no real charges are authorized.
- One durable Linux controller only; no stateless Vercel backend or multi-instance
  database/storage design is claimed.
- Backup infrastructure, encrypted disks, restore drills, external alerting and
  complete self-service deletion remain operator/future work.
- Candidate evidence is customer-supplied. No arbitrary agent code execution,
  deterministic model rerun or cryptographic execution attestation is provided.
- Business demand, paid continuation and pricing remain unvalidated.

Source packaging and GitHub CI are checked separately on the final commit; do not
infer a remote CI result solely from these local tests.
