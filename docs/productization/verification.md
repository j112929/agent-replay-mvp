# Verified scope and remaining work

Local verification on 2026-10-11, Python 3.12:

| Check | Actual result |
| --- | --- |
| Full `python -m unittest discover -s tests -v` | 117 tests passed, no skips |
| Guard-focused import/generation/approval/API/CLI tests | 23 passed |
| `node --test tests/*.test.mjs` | 10 passed |
| Published schemas | Well formed; generated case, fixtures, trajectories and results validate |
| `python scripts/package_release.py --check` | Source ZIP, wheel assets, schemas and plugin vendor copies agree |
| Wheel build and installation in an empty virtual environment | Built; installed without network or optional/runtime dependencies |
| Installed CLI `guard demo` | PASS, FAIL, blocking INCONCLUSIVE; verified pair and explicit synthetic approval |
| Plugin script `guard demo` | Same result from the bundled vendor SDK |
| Network/process trap during guard evaluation | No network request or subprocess invoked |
| Canary scan across capture/journal/import/case/fixture/report files | Synthetic token absent |
| CLI release decision | PASS exits 0; regression exits 1; absent candidate exits 3 |
| Tenant HTTP integration | Foreign cases/runs unavailable; VIEWER cannot mutate; ADMIN approval uses authenticated subject |
| Storage limit at final report commit | Oversized staged report rejected and temporary artifacts removed |

The full suite covers the existing benchmark, capture, regression, replay, rollout,
tenant and server workflows. GPU/distributed training is not locally validated by
this change; its existing CI jobs are preserved. An existing static HTTP asset test
can log a client-disconnect BrokenPipe while remaining successful.

## Incremental changes

Added `guard/{imports,generator,store,runner,cli,demo}.py` and restricted declarative
predicates. Extended the existing assertion engine, case evaluator, tenant API,
access configuration, CLI dispatcher and regression case schema. Fixed sanitizer
placeholder idempotence; exact assertions distinguish nested booleans/numbers.
Added focused tests, synthetic CI workflow, customer workflow template and guide.
Synchronized the existing plugin vendor source and public ZIP using the existing
packaging script. No database migration or new runtime dependency is required.

Assessment, product requirements, architecture decisions, pricing/discovery
templates, migration/rollback/cost plans, security gaps, operator runbook and risk
register were written before the vertical slice. The initial assessment is retained
as the pre-implementation baseline; this document records the delivered scope.

## CI interpretation

The repository's new workflow proves that the synthetic failing candidate exits 1,
publishes the FAIL decision and uploads synthetic evidence. Its validation job should
be green because it asserts that release blocking works. A customer must configure
the provided direct `guard run` workflow as a required check; that job becomes red
for FAIL or INCONCLUSIVE. No customer repository, required-check policy or automatic
GitHub comment has been configured or claimed.

## Not a complete commercial SaaS

This meets the requested single-test offline vertical slice. No real design partners,
pricing validation, signup/invites, hosted multi-instance API, billing, general agent
framework execution or new product UI lifecycle has been implemented. Approval
validates the supplied example pair only. Candidate captures come from trusted
customer CI; the platform does not attest or rerun arbitrary code. Model rerun and
unrestricted execution remain disabled. Local audit retention, encrypted hosted
backups and per-record deletion remain operator/future work. No production release
or real payment transaction is performed.
