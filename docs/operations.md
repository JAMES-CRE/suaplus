# Operator runbook

## Prerequisites

Provision an approved host, TLS proxy, persistent volumes, monitoring destination and secret store. Obtain UITS/provider access separately. Never use example secrets or unauthorized production records in staging.

Copy `.env.deploy.example` to untracked `.env.deploy`. Set a random Django secret of at least 50 characters, DB password, explicit hosts and HTTPS SITE_URL. Before enabling TRUST_PROXY_TLS, configure the proxy to strip client-supplied forwarding headers. DB/cache ports stay private.

## Startup

```sh
docker compose --env-file .env.deploy config --quiet
docker compose --env-file .env.deploy build
docker compose --env-file .env.deploy up -d database redis migrate
docker compose --env-file .env.deploy up -d web worker beat
docker compose --env-file .env.deploy ps
```

The migration service completes once before web/workers. Run exactly one Beat instance. DEPLOY_ENV_FILE can select another file and must match --env-file. Gunicorn runs as UID 10001; WhiteNoise serves fingerprinted assets. Private media is accessed through authorized views, not a public mount.

Record image digest and schema revision before promotion. Stop without deleting persistent volumes. Never use compose down -v for release rollback. Review old-image/new-schema compatibility before rollback; database restoration needs explicit authorization and an agreed data-loss window.

## Monitoring

- /live/: process HTTP liveness.
- /health/: database connection compatibility endpoint.
- /ready/: SQL query, shared-cache write/read and worker heartbeat. Beat dispatches every minute; only worker execution refreshes a 180-second heartbeat. Missing dependency returns 503.
- Alert on repeated readiness failures, queue/worker failure, delivery failure, migration/backup failure, stale backup, and disk capacity.
- Readiness does not cover every worker/provider. Add platform metrics and actual delivery acceptance.
- Redact tokenized URL paths/queries, certificate contents, credentials and payloads in proxy/application logs. Do not send sensitive request paths to a centralized log service.

## Badges

Generate approved RSA keys outside this repository. Mount private/public PEM files read-only with an operator-controlled Compose override, set BADGE_PRIVATE_KEY_FILE/BADGE_PUBLIC_KEY_FILE/BADGE_KEY_ID. Never include keys in images.

/badges/public-key/ returns public PEM or 503 when disabled. Production cannot use the Django secret to sign awards. Plan verification-key rotation/preservation before live issuance; external OB3 verifier acceptance is a separate task.

## Backup and restore

```sh
docker compose --env-file .env.deploy --profile backup run --rm backup
```

Schedule this one-shot command daily in the approved host scheduler and alert on failure. It produces custom-format dumps, restrictive permissions, format validation and checksum. Interrupted .partial files are not complete backups. Replicate encrypted DB backups and coordinated media archives off-host.

Maintain the approved 30-day retention policy. scripts/backup_inventory.sh only lists old candidates; automatic deletion is disabled until off-host replication and a restore drill are verified. Approve pruning separately and monitor capacity.

Restore into an operator-created empty disposable DB. Set RESTORE_DB_NAME and CONFIRM_RESTORE to the exact target; the target cannot equal the application DB_NAME. Run `bash scripts/restore_db.sh /absolute/backup.dump` with the .sha256 file present and proper DB host/user/password. No --clean/--create is used; restore is transactional and exits on error.

Compare restored counts, selected KPI results, identities/consent, credentials and media references; record RPO/RTO. Format/checksum validation is not a restore drill. Full DB+media recovery and scheduler/alert behavior remain staging acceptance exercises.

Scheduler examples are provided in scripts/backup.service.example and scripts/backup.timer.example; an operator must install/enable them on the approved host. scripts/backup_media.sh archives authorized media to the restricted backup destination; use the same maintenance window as the DB dump for coherent recovery. Neither scheduling nor off-host replication is enabled by writing these files.
