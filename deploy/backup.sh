#!/usr/bin/env bash
#
# Take a consistent, compressed (optionally encrypted) backup of Waggle's
# SQLite database and copy it out of the control-plane volume.
#
#   sudo ./deploy/backup.sh
#
# Environment:
#   WAGGLE_BACKUP_DIR=/var/backups/waggle
#   WAGGLE_BACKUP_KEEP_DAYS=14
#   WAGGLE_BACKUP_AGE_RECIPIENT=age1…   # encrypt with age when set
#   WAGGLE_ENV_FILE=deploy/.env

set -euo pipefail

log() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

SCRIPT="${BASH_SOURCE[0]}"
DEPLOY_DIR="$(cd "$(dirname "${SCRIPT}")" && pwd)"
ENV_FILE="${WAGGLE_ENV_FILE:-${DEPLOY_DIR}/.env}"
DEST_DIR="${WAGGLE_BACKUP_DIR:-/var/backups/waggle}"
KEEP_DAYS="${WAGGLE_BACKUP_KEEP_DAYS:-14}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="waggle-${STAMP}.sqlite3"

[ -f "${ENV_FILE}" ] || die "cannot find ${ENV_FILE}."
if [ "$(id -u)" -eq 0 ]; then SUDO=""; elif command -v sudo >/dev/null 2>&1; then SUDO="sudo"; else die "run as root or install sudo."; fi

compose() { ${SUDO} docker compose --env-file "${ENV_FILE}" -f "${DEPLOY_DIR}/docker-compose.yml" "$@"; }

${SUDO} mkdir -p "${DEST_DIR}"
chmod 700 "${DEST_DIR}" 2>/dev/null || true

log "Writing an online backup inside the control plane"
compose exec -T web python manage.py backup_data "/var/lib/waggle/${NAME}"

TEMP="$(mktemp "${DEST_DIR}/.waggle-XXXXXX")"
trap 'rm -f "${TEMP}"' EXIT
compose cp "web:/var/lib/waggle/${NAME}" "${TEMP}" >/dev/null
compose exec -T web rm -f "/var/lib/waggle/${NAME}"

if [ -n "${WAGGLE_BACKUP_AGE_RECIPIENT:-}" ]; then
    command -v age >/dev/null 2>&1 || die "WAGGLE_BACKUP_AGE_RECIPIENT is set but age is not installed."
    age --encrypt --recipient "${WAGGLE_BACKUP_AGE_RECIPIENT}" --output "${DEST_DIR}/${NAME}.age" "${TEMP}"
    rm -f "${TEMP}"
    FINAL="${DEST_DIR}/${NAME}.age"
else
    gzip -9 -c "${TEMP}" >"${DEST_DIR}/${NAME}.gz"
    rm -f "${TEMP}"
    FINAL="${DEST_DIR}/${NAME}.gz"
fi
trap - EXIT
chmod 600 "${FINAL}"
( cd "${DEST_DIR}" && sha256sum "$(basename "${FINAL}")" >"$(basename "${FINAL}").sha256" )

log "Pruning backups older than ${KEEP_DAYS} days"
find "${DEST_DIR}" -maxdepth 1 -type f \( -name 'waggle-*.sqlite3*' -o -name 'waggle-*.sha256' \) \
    -mtime "+${KEEP_DAYS}" -delete

log "Backup written to ${FINAL}"
