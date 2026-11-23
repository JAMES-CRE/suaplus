#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
: "${MEDIA_DIR:?Set MEDIA_DIR to the authorized media directory}"
: "${BACKUP_DIR:?Set BACKUP_DIR to a restricted backup directory}"
[[ -d "$MEDIA_DIR" ]] || { printf 'Media directory missing\n' >&2; exit 2; }
mkdir -p "$BACKUP_DIR"
output="$BACKUP_DIR/media-$(date -u +%Y%m%dT%H%M%SZ)-$$.tar.gz"
tar --create --gzip --file="$output.partial" --directory="$MEDIA_DIR" .
tar --list --gzip --file="$output.partial" >/dev/null
mv -- "$output.partial" "$output"
sha256sum "$output" > "$output.sha256"
printf 'MEDIA_BACKUP_OK %s\n' "$output"
