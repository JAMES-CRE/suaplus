# Staging acceptance

**Not signed off.** An authorized reviewer must execute each exercise and record host/image, date, owner and evidence. Local verification is not permission to deploy or institutional acceptance.

| Area | Staging exercise / expected result | Status |
|---|---|---|
| Deployment | Approved image/environment, migrations once; non-root web, persistent/private DB/cache/media and static assets | Pending |
| Security | TLS, explicit hosts, secure cookies, CSRF and proxy header stripping | Pending |
| Roles | Authorized/cross-scope view, write, export and admin attempts; data outside scope denied | Pending |
| MFA | New login with real TOTP; enrolled but unverified admin cannot write; replay rejected | Pending |
| Identity/consent | Identifier variants/shared contacts/ambiguity/minors; canonical records, no candidate disclosure, guardian consent and no minor phone | Pending |
| Completion | Exit success/replay/cancelled workshop; atomic timestamps/Level 1 evidence, no duplicate feedback | Pending |
| Imports | Mapping/preview/existing/number-only identity/commit; scoped writes, no preview mutations | Pending |
| Undo | Wrong batch/replay/mixed scope/later completion or awards; history retained, dependencies rejected atomically | Pending |
| SAS | Delivered link, invalid profile/answers, all questions, expiry/replay; one self submission and no verified credit | Pending |
| Internal awards | Completed task, rule caps, wrong framework/self/cross-scope/replay; source-linked assessed award and audit rationale | Pending |
| External credentials | Upload/review/return/resubmit/expiry/private files; framework-specific idempotent evidence | Pending |
| Badges | Approved keys, independent verifier, revocation and rotation; dedicated signing and OB3 interoperability evidence | Pending |
| Trainer engine | Controlled criteria/inactivity/resumed activity; no expired/self/foreign-programme credit and nominations preserved | Pending |
| Reporting | Reconcile headcount snapshots, chronological evidence and cascade with exports/memos; scope/period/suppression consistent | Pending |
| Providers | Approved SMTP/Google/Registry/SMS/memo sandbox; actual delivery/auth/sync and correct fallback, no data leakage | Pending |
| Workers/monitoring | Worker/Beat outage, queues and failed delivery; readiness/alerts/recovery observed | Pending |
| Recovery | Off-host DB/media backup and restore into empty disposable target; records/files/RPO/RTO verified | Pending |
| Rollback | Reviewed image/schema compatibility; safe rollback without data deletion | Pending |
| Institutional review | Product owner/UITS/privacy reviewers record acceptance and exceptions | Pending |

Attach exact pytest/per-module coverage, UI/CSS/static checks, migration checks and real HTTP smoke output. Mark dependency permissions, unavailable credentials/host/signing keys, and unexecuted container/restore checks explicitly. Do not mark rows accepted from this document or unit tests alone.
