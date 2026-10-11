# Retention and deletion scope

Organization pilot configuration uses `retention_days` as an **inactivity threshold**,
not a guaranteed maximum age per individual record. ADMIN may preview/apply cleanup:

```text
POST /api/v1/retention
{"apply": false}
```

If every artifact in the organization root is older than the threshold, applying
cleanup deletes the entire inactive artifact cohort: trajectories, journals, jobs,
benchmark runs, reports, comparisons, imported cases and policy artifacts. This
conservative cohort design avoids dangling evidence and derived records. Active
in-memory jobs block cleanup; cached job/cancellation/idempotency maps are cleared
after deletion. The service has no search index or trajectory cache. Browser evidence
caching has been removed; existing historical browser caches require manual removal.
The audit log records the operation outside the deleted namespace.

For an active tenant, older records remain until the whole cohort is eligible. This
is not a per-record legal-erasure SLA. Granular dependency-aware deletion and active
retention scheduling are future work. No automatic timer is installed.

The legacy `cleanup` CLI remains a single-user standalone trace cleaner, with its
original protected baseline behavior. It is not the pilot organization-erasure API.

Audit records have separate retention, currently operator-managed. External backups,
filesystem snapshots, exported bundles, GitHub Actions artifacts, screenshots and
third-party storage replicas are not deleted by this API. Set backup expiration and
access restrictions with those systems; restores must honor deletion tombstones or
be re-sanitized/reconciled before serving. The included security CI evidence uses
GitHub artifact retention of 14 days. No cross-system erasure guarantee is claimed.
