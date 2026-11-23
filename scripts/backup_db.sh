#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
: "${DB_NAME:?DB_NAME required}" "${DB_USER:?DB_USER required}" "${DB_HOST:?DB_HOST required}"
export PGPASSWORD="${DB_PASSWORD:?DB_PASSWORD required}"
export PGCONNECT_TIMEOUT=10
BACKUP_DIR="${BACKUP_DIR:-/var/backups/suaplus}"
mkdir -p "$BACKUP_DIR"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
output="$BACKUP_DIR/backup-$stamp-$$.dump"
partial="$output.partial"
pg_dump --host="$DB_HOST" --port="${DB_PORT:-5432}" --username="$DB_USER" --dbname="$DB_NAME" --format=custom --file="$partial" --no-owner --no-acl
# Listing validates format, not recoverability: a separate restore drill is required.
pg_restore --list "$partial" >/dev/null
mv -- "$partial" "$output"
sha256sum "$output" > "$output.sha256"
printf 'BACKUP_OK %s\n' "$output"
# Retention deletion is deliberately not automatic. Reviewed backup inventory,
# off-host replication and a successful restore drill must precede pruning.
