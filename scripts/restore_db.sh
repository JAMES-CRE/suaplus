#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
: "${RESTORE_DB_NAME:?Set a separate disposable RESTORE_DB_NAME}"
: "${DB_USER:?DB_USER required}" "${DB_HOST:?DB_HOST required}"
[[ "${CONFIRM_RESTORE:-}" == "$RESTORE_DB_NAME" ]] || { printf 'Set CONFIRM_RESTORE to the exact disposable target\n' >&2; exit 2; }
[[ "$RESTORE_DB_NAME" != "${DB_NAME:-}" ]] || { printf 'Refusing restore over the application database\n' >&2; exit 2; }
input="${1:?Pass a backup file}"
[[ -f "$input" && -f "$input.sha256" ]] || { printf 'Backup and checksum required\n' >&2; exit 2; }
sha256sum --check "$input.sha256"
pg_restore --list "$input" >/dev/null
export PGPASSWORD="${DB_PASSWORD:?DB_PASSWORD required}"
export PGCONNECT_TIMEOUT=10
# No --clean or --create: target must be an authorized, empty disposable DB.
pg_restore --host="$DB_HOST" --port="${DB_PORT:-5432}" --username="$DB_USER" --dbname="$RESTORE_DB_NAME" --no-owner --no-acl --single-transaction --exit-on-error "$input"
printf 'RESTORE_OK %s\n' "$RESTORE_DB_NAME"
