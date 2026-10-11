# Remaining risk register

| Risk | Severity | Control / next evidence | Status |
| --- | --- | --- | --- |
| No verified isolation for executable agents | critical | deny host replay; consume candidate evidence only | executable workloads deferred |
| Generated verifier overfits example pair | high | explicit expectations, positive/negative validation and review; obtain held-out examples | general validity unproven |
| Partial imported traces | high | retain missing payloads/parents as errors or limitations; block unsupported fixtures | targeted tests required |
| Candidate evidence is forged or misleading | high | checksums/task identity/version checks; customer CI is trusted capture boundary | no execution attestation |
| Unknown secrets / historical raw evidence | high | configured fields, fail-closed imports, explicit migration | complete detection not claimed |
| File controller concurrency/quotas | high | one controller, bounded files and OS lock; transactional store before multi-instance | hosted service deferred |
| Local audit/backups can be read by OS admin | high | file permissions, restricted backups; managed encryption/KMS later | not enterprise compliance |
| Public CI artifacts leak data | high | synthetic fixture CI only; sanitized reports; private artifacts for real customers | operator configuration required |
| Billing duplicate/out-of-order events | high | signed test webhooks and event ledger before entitlement changes | billing absent |
| No product demand / price validation | high | ten interviews, three partners, two paid continuations | no customer evidence |
| Excessive support burden | medium | standard integrations, measure support minutes, no custom branches | 5–10 hours/week unverified |
| Static site looks like hosted SaaS | medium | label local API requirements; no remote API claim | hosting unchanged |
| Gate not required by branch protection | high | owner configures named required check | not automatically enforced on deployments |
