#!/usr/bin/env bash
#
# Back up or restore one local Docker volume.
#
# Run this on the node that currently holds the volume. Swarm's local volume
# driver does not replicate data, so the control plane cannot do this for you.
# Application tasks are not stopped; stop the service first for a fully
# consistent application-level backup.
#
#   sudo ./deploy/volume-backup.sh backup  waggle-service-12-api-data /var/backups/waggle/volumes
#   sudo ./deploy/volume-backup.sh restore waggle-service-12-api-data /var/backups/waggle/volumes/….tgz --yes

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  volume-backup.sh backup  VOLUME DEST_DIR
  volume-backup.sh restore VOLUME ARCHIVE.tgz --yes

Run on the Docker node that holds the volume.
EOF
}

log() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

absolute() {
    case "$1" in
        /*) printf '%s' "$1" ;;
        *) printf '%s/%s' "$(pwd)" "$1" ;;
    esac
}

[ "$#" -ge 3 ] || { usage; exit 1; }
MODE="$1"; VOLUME="$2"; TARGET="$(absolute "$3")"; shift 3
case "${MODE}" in backup|restore) ;; *) usage; die "unknown mode: ${MODE}";; esac
printf '%s' "${VOLUME}" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9_.-]*$' ||
    die "invalid volume name."
command -v docker >/dev/null 2>&1 || die "docker is required."

if [ "$(id -u)" -eq 0 ]; then SUDO=""; elif command -v sudo >/dev/null 2>&1; then SUDO="sudo"; else die "run as root or install sudo."; fi
docker volume inspect "${VOLUME}" >/dev/null 2>&1 || die "no such volume: ${VOLUME}"

if [ "${MODE}" = "backup" ]; then
    ${SUDO} mkdir -p "${TARGET}"
    ${SUDO} chmod 700 "${TARGET}"
    NAME="${VOLUME}-$(date -u +%Y%m%dT%H%M%SZ).tgz"
    log "Backing up ${VOLUME} to ${TARGET}/${NAME}"
    ${SUDO} docker run --rm --user 0 \
        -v "${VOLUME}:/data:ro" -v "${TARGET}:/backup" \
        alpine:3.20 sh -eu -c 'tar czf "/backup/$1" -C /data .' sh "${NAME}"
    ${SUDO} chmod 600 "${TARGET}/${NAME}"
    ${SUDO} sh -eu -c 'cd "$1" && sha256sum "$2" >"$2.sha256"' sh "${TARGET}" "${NAME}"
    log "Wrote ${TARGET}/${NAME} and its SHA-256 file"
    exit 0
fi

[ "$#" -ge 1 ] && [ "$1" = "--yes" ] || die "restore overwrites data; pass --yes to confirm."
[ -f "${TARGET}" ] || die "cannot find ${TARGET}."
ARCHIVE="${TARGET}"

if [ -f "${ARCHIVE}.sha256" ]; then
    ${SUDO} sh -eu -c 'cd "$1" && sha256sum -c "$2"' sh "${ARCHIVE%/*}" "${ARCHIVE##*/}.sha256" ||
        die "the archive checksum did not verify."
fi

log "Checking the archive"
${SUDO} docker run --rm -v "${ARCHIVE%/*}:/backup:ro" alpine:3.20 \
    tar tzf "/backup/${ARCHIVE##*/}" >/dev/null || die "the archive is not a readable tar.gz."

log "Restoring ${VOLUME} from ${ARCHIVE}"
${SUDO} docker run --rm --user 0 \
    -v "${VOLUME}:/data" -v "${ARCHIVE%/*}:/backup:ro" \
    alpine:3.20 sh -eu -c 'tar xzf "/backup/$1" -C /data' sh "${ARCHIVE##*/}"
log "Restore complete. Start or redeploy the service."
