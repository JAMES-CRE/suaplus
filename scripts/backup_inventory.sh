#!/usr/bin/env bash
set -Eeuo pipefail
# Review-only retention inventory; never deletes backups automatically.
BACKUP_DIR="${BACKUP_DIR:?Set BACKUP_DIR}"
retention="${BACKUP_RETENTION_DAYS:-30}"
[[ "$retention" =~ ^[0-9]+$ ]] || { printf 'Retention must be an integer\n' >&2; exit 2; }
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'backup-*.dump' -mtime +"$retention" -print
