#!/usr/bin/env bash
#
# Upgrade Waggle to a pinned release image, with a database backup and an
# automatic rollback if the new version never becomes healthy.
#
#   sudo ./deploy/upgrade.sh --image ghcr.io/OWNER/waggle@sha256:…
#
# Environment:
#   WAGGLE_ENV_FILE=deploy/.env
#   WAGGLE_REQUIRE_SIGNATURE=1
#   WAGGLE_COSIGN_IDENTITY=https://github.com/OWNER/REPO/.github/workflows/release.yml@refs/tags/v1.2.3
#   WAGGLE_HEALTH_TIMEOUT=180

set -euo pipefail

log()  { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

IMAGE=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --image) [ "$#" -ge 2 ] || die "--image needs a reference."; IMAGE="$2"; shift 2 ;;
        --env) [ "$#" -ge 2 ] || die "--env needs a path."; WAGGLE_ENV_FILE="$2"; shift 2 ;;
        -h|--help)
            printf '%s\n' "Usage: sudo ./deploy/upgrade.sh --image repo@sha256:…"
            exit 0
            ;;
        *) die "Unknown option: $1" ;;
    esac
done

[ "$(uname -s)" = "Linux" ] || die "upgrade.sh targets a Linux manager."
[ -n "${IMAGE}" ] || die "--image is required."
case "${IMAGE}" in
    *@sha256:*) ;;
    *)
        [ "${WAGGLE_ALLOW_UNPINNED:-0}" = "1" ] || \
            die "release images must be pinned by digest (repo@sha256:…)."
        warn "upgrading to an unpinned image: ${IMAGE}"
        ;;
esac

if [ "$(id -u)" -eq 0 ]; then SUDO=""; elif command -v sudo >/dev/null 2>&1; then SUDO="sudo"; else die "run as root or install sudo."; fi

SCRIPT="${BASH_SOURCE[0]}"
DEPLOY_DIR="$(cd "$(dirname "${SCRIPT}")" && pwd)"
ENV_FILE="${WAGGLE_ENV_FILE:-${DEPLOY_DIR}/.env}"
[ -f "${ENV_FILE}" ] || die "cannot find ${ENV_FILE}."
[ -f "${DEPLOY_DIR}/docker-compose.yml" ] || die "cannot find deploy/docker-compose.yml."

compose() { ${SUDO} docker compose --env-file "${ENV_FILE}" -f "${DEPLOY_DIR}/docker-compose.yml" "$@"; }

set_env_value() {
    local key="$1" value="$2" temp
    temp="$(mktemp "${ENV_FILE}.XXXXXX")"
    awk -v key="${key}" -v value="${value}" '
        BEGIN { found = 0 }
        $0 ~ "^" key "=" { print key "=" value; found = 1; next }
        { print }
        END { if (!found) print key "=" value }
    ' "${ENV_FILE}" >"${temp}"
    chmod 600 "${temp}" 2>/dev/null || true
    mv "${temp}" "${ENV_FILE}"
}

verify_signature() {
    if [ -n "${WAGGLE_IMAGE_DIGEST:-}" ]; then
        local actual="${IMAGE##*@}"
        [ "${actual}" = "${WAGGLE_IMAGE_DIGEST}" ] ||
            die "image digest mismatch: expected ${WAGGLE_IMAGE_DIGEST}, got ${actual}."
    fi
    if command -v cosign >/dev/null 2>&1 && [ -n "${WAGGLE_COSIGN_IDENTITY:-}" ]; then
        log "Verifying the release signature"
        cosign verify \
            --certificate-identity "${WAGGLE_COSIGN_IDENTITY}" \
            --certificate-oidc-issuer "${WAGGLE_COSIGN_ISSUER:-https://token.actions.githubusercontent.com}" \
            "${IMAGE}" >/dev/null || die "the image signature did not verify."
    elif [ "${WAGGLE_REQUIRE_SIGNATURE:-0}" = "1" ]; then
        die "WAGGLE_REQUIRE_SIGNATURE=1 requires cosign and WAGGLE_COSIGN_IDENTITY."
    else
        warn "signature verification skipped; set WAGGLE_REQUIRE_SIGNATURE=1 in production."
    fi
}

wait_for_health() {
    local deadline=$((SECONDS + ${WAGGLE_HEALTH_TIMEOUT:-180})) port
    port="$(sed -n 's/^WAGGLE_HTTP_PORT=//p' "${ENV_FILE}" | tail -n 1)"
    port="${port##*:}"
    port="${port:-8000}"
    while [ "${SECONDS}" -lt "${deadline}" ]; do
        if curl -fsS --max-time 4 -H 'Host: localhost' -H 'X-Forwarded-Proto: https' \
            "http://127.0.0.1:${port}/readyz" >/dev/null 2>&1; then
            return 0
        fi
        sleep 3
    done
    return 1
}

OLD_IMAGE="$(compose ps -q web 2>/dev/null | head -n 1 | xargs -r ${SUDO} docker inspect --format '{{.Config.Image}}' 2>/dev/null || true)"
[ -n "${OLD_IMAGE}" ] || OLD_IMAGE="$(sed -n 's/^WAGGLE_IMAGE=//p' "${ENV_FILE}" | tail -n 1)"
[ -n "${OLD_IMAGE}" ] || OLD_IMAGE="waggle:latest"

verify_signature

log "Backing up the database before upgrading"
${SUDO} mkdir -p "${DEPLOY_DIR}/backups"
compose exec -T web python manage.py backup_data "/var/lib/waggle/backups/pre-upgrade-$(date -u +%Y%m%dT%H%M%SZ).sqlite3" ||
    die "the pre-upgrade backup failed; nothing was changed."

log "Upgrading ${OLD_IMAGE} -> ${IMAGE}"
set_env_value WAGGLE_IMAGE "${IMAGE}"
compose pull web worker
compose up -d --no-build --remove-orphans

if wait_for_health; then
    log "Upgrade healthy. Old image was ${OLD_IMAGE}."
    exit 0
fi

warn "the new version did not become healthy; rolling back to ${OLD_IMAGE}"
set_env_value WAGGLE_IMAGE "${OLD_IMAGE}"
compose pull web worker || true
compose up -d --no-build --remove-orphans
if wait_for_health; then
    die "upgrade failed and was rolled back to ${OLD_IMAGE}."
fi
die "upgrade failed AND the rollback is unhealthy. Check: docker compose -f ${DEPLOY_DIR}/docker-compose.yml logs web"
