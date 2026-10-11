# Agent Regression Guard operator runbook

Scope: local/offline design-partner demonstration, one controller. Vercel serves
static UI and SDK; the Python API is not a hosted SaaS. No new production deployment
or live billing occurs as part of this slice.

## Daily checks and support boundaries

Inspect /api/v1/health on the loopback service and exported project usage. Unexpected
INCONCLUSIVE requires missing identity, source checksum, fixture and approval checks,
not retries with live credentials. Do not print trace bodies, Authorization headers
or customer tokens while troubleshooting. No model spending occurs in recorded
playback/evidence evaluation. A repeated job uses a bounded request and cannot
start arbitrary customer code. Keep support minutes and incidents in the partner log.

## Backups and restore procedure

Stop mutations first; copy the complete sanitized organization root plus approval
history and separately managed audit/config into an operator-restricted encrypted
backup. Credentials/access config belong in a separate secret manager, never public
artifacts. Verify content hashes on restore; test imports, approval binding and an
offline gate before re-enabling writes. This procedure is documented, not a proven
cloud backup/restore automation. Record restore date, operator, hash verification and
results. Local copies are not encryption at rest by themselves.

## Retention, incidents and rollback

Existing ADMIN retention removes entire inactive tenant artifact cohorts, including
guard data, and leaves separately retained audit. Backups, downloaded exports and
GitHub artifact expiry require operator handling; active per-record deletion is not
implemented. Review legal retention before promising deletion timelines.

If a secret leaks, pause ingestion, revoke the affected credential outside this
application, investigate restricted audit, sanitize/delete affected artifacts and
derived copies, and review backup/CI copies. Never attempt to reconstruct redacted
secrets. If approval/checksums differ, quarantine the case and require regeneration
and explicit review. During rollback disable generated-suite evaluation on older
packages. Preserve the tested privacy boundary and never enable host replay.

## Commercial launch gates

Before hosted design partners: transactional account/usage metadata, tenant tests,
TLS and storage encryption, bounded retention, restore rehearsal and alerts. Before
billing: Stripe test-mode signed webhook/duplicate/out-of-order tests and operator
approval of exact live settings. Before release: all required CI checks plus owner
configured branch/deployment gates. No automatic emails/invites or commercial charges
are sent by this implementation.
