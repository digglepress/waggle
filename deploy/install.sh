#!/usr/bin/env bash
#
# Waggle installer — run this on the machine that will be the Swarm manager.
#
#   curl -fsSL "$WAGGLE_INSTALL_URL" | sudo bash -s -- --source "$WAGGLE_REPO_URL" --ref "$WAGGLE_REF"
#
# or, from a checkout:
#
#   sudo ./deploy/install.sh
#
# It installs Docker Engine if needed, initialises the swarm, generates a
# secret key, builds the control plane and starts it with Compose. It is
# idempotent: re-running it updates the running control plane in place.
#
# Environment overrides:
#   WAGGLE_HTTP_PORT=127.0.0.1:8000   host publish address and port for the UI
#   WAGGLE_ALLOWED_HOSTS=localhost   Django ALLOWED_HOSTS (comma separated)
#   WAGGLE_SECURE_COOKIES=0        set to 1 once TLS terminates in front
#   WAGGLE_ADMIN_USER=admin
#   WAGGLE_ADMIN_PASSWORD=...      if unset, prompt or generate a password
#   WAGGLE_SWARM_ADVERTISE_ADDR=   address other nodes should use

set -euo pipefail

log()  { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------------------- #
# Privileges
# --------------------------------------------------------------------------- #

SOURCE=""
REF=""
IMAGE="${WAGGLE_IMAGE:-}"
OFFLINE="${WAGGLE_OFFLINE:-}"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --source) [ "$#" -ge 2 ] || die "--source needs a repository URL."; SOURCE="$2"; shift 2 ;;
        --ref) [ "$#" -ge 2 ] || die "--ref needs a release tag or branch."; REF="$2"; shift 2 ;;
        --image) [ "$#" -ge 2 ] || die "--image needs a pinned image reference."; IMAGE="$2"; shift 2 ;;
        --offline) [ "$#" -ge 2 ] || die "--offline needs an image tarball."; OFFLINE="$2"; shift 2 ;;
        -h|--help)
            printf '%s\n' "From a checkout: sudo bash deploy/install.sh" \
                "Release install: sudo bash deploy/install.sh --image ghcr.io/OWNER/waggle@sha256:…" \
                "Offline install: sudo bash deploy/install.sh --image … --offline waggle-image.tar" \
                "From stdin: sudo bash -s -- --source https://…/repo.git --ref RELEASE_TAG [--image …]"
            exit 0
            ;;
        *) die "Unknown option: $1" ;;
    esac
done

if [ "$(uname -s)" != "Linux" ]; then
    die "Use a Linux manager for install.sh. For a local Docker Desktop lab, use deploy/lab.sh."
fi
if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
elif command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
else
    die "run as root, or install sudo."
fi

if [ -n "${SOURCE}" ]; then
    case "${SOURCE}" in https://*) ;; *) die "--source must be a trusted HTTPS Git repository." ;; esac
    [ -n "${REF}" ] || die "Set --ref to a trusted release tag or branch."
    TARGET="${WAGGLE_INSTALL_DIR:-/opt/waggle}"
    case "${TARGET}" in /*) ;; *) die "WAGGLE_INSTALL_DIR must be an absolute path." ;; esac
    [ ! -e "${TARGET}" ] && [ ! -L "${TARGET}" ] ||
        die "${TARGET} already exists. Run its deploy/install.sh for an existing installation."
    if ! command -v git >/dev/null 2>&1; then
        command -v apt-get >/dev/null 2>&1 || die "Install Git before running this script."
        ${SUDO} apt-get update
        ${SUDO} apt-get install -y --no-install-recommends git ca-certificates
    fi
    log "Fetching Waggle ${REF} into ${TARGET}"
    ${SUDO} git clone --depth 1 --branch "${REF}" -- "${SOURCE}" "${TARGET}"
    exec ${SUDO} env ${IMAGE:+WAGGLE_IMAGE=${IMAGE}} ${OFFLINE:+WAGGLE_OFFLINE=${OFFLINE}} bash "${TARGET}/deploy/install.sh"
fi
[ -z "${REF}" ] || die "--ref requires --source."

SCRIPT="${BASH_SOURCE[0]:-}"
[ -f "${SCRIPT}" ] || die "When piping this installer, provide --source and --ref."
REPO_DIR="$(cd "$(dirname "${SCRIPT}")/.." && pwd)"
DEPLOY_DIR="${REPO_DIR}/deploy"
ENV_FILE="${DEPLOY_DIR}/.env"
HTTP_BIND="${WAGGLE_HTTP_PORT:-127.0.0.1:8000}"
PROXY_NETWORK="${WAGGLE_PROXY_NETWORK:-paas_proxy}"

if [ ! -f "${DEPLOY_DIR}/docker-compose.yml" ]; then
    die "cannot find deploy/docker-compose.yml next to install.sh (${DEPLOY_DIR})."
fi

compose() {
    if ${SUDO} docker compose version >/dev/null 2>&1; then
        ${SUDO} docker compose --env-file "${ENV_FILE}" -f "${DEPLOY_DIR}/docker-compose.yml" "$@"
    elif command -v docker-compose >/dev/null 2>&1; then
        ${SUDO} docker-compose --env-file "${ENV_FILE}" -f "${DEPLOY_DIR}/docker-compose.yml" "$@"
    else
        die "the Docker Compose plugin is missing. Install docker-compose-plugin."
    fi
}

# --------------------------------------------------------------------------- #
# Release image verification
# --------------------------------------------------------------------------- #

require_pinned_image() {
    [ -n "${IMAGE}" ] || return 0
    case "${IMAGE}" in
        *@sha256:*) ;;
        *)
            if [ "${WAGGLE_ALLOW_UNPINNED:-0}" != "1" ]; then
                die "release images must be pinned by digest (repo@sha256:…); set WAGGLE_ALLOW_UNPINNED=1 only for local testing."
            fi
            warn "installing an unpinned image: ${IMAGE}"
            ;;
    esac
}

verify_image() {
    [ -n "${IMAGE}" ] || return 0
    if [ -n "${WAGGLE_IMAGE_DIGEST:-}" ]; then
        local actual="${IMAGE##*@}"
        [ "${actual}" = "${WAGGLE_IMAGE_DIGEST}" ] ||
            die "image digest mismatch: expected ${WAGGLE_IMAGE_DIGEST}, got ${actual}."
    fi
    if [ -n "${OFFLINE}" ]; then
        if [ "${WAGGLE_REQUIRE_SIGNATURE:-0}" = "1" ]; then
            warn "offline install verifies the tarball checksum, not the registry signature; verify the digest before transfer."
        fi
        return 0
    fi
    if command -v cosign >/dev/null 2>&1; then
        if [ -n "${WAGGLE_COSIGN_IDENTITY:-}" ]; then
            log "Verifying the release signature"
            cosign verify \
                --certificate-identity "${WAGGLE_COSIGN_IDENTITY}" \
                --certificate-oidc-issuer "${WAGGLE_COSIGN_ISSUER:-https://token.actions.githubusercontent.com}" \
                "${IMAGE}" >/dev/null ||
                die "the image signature did not verify."
        elif [ "${WAGGLE_REQUIRE_SIGNATURE:-0}" = "1" ]; then
            die "WAGGLE_REQUIRE_SIGNATURE=1 requires WAGGLE_COSIGN_IDENTITY."
        fi
    elif [ "${WAGGLE_REQUIRE_SIGNATURE:-0}" = "1" ]; then
        die "WAGGLE_REQUIRE_SIGNATURE=1 requires cosign on PATH."
    fi
}

load_offline_image() {
    [ -n "${OFFLINE}" ] || return 0
    [ -n "${IMAGE}" ] || die "--offline also needs the --image reference it contains."
    [ -f "${OFFLINE}" ] || die "cannot find the offline image tarball ${OFFLINE}."
    if [ -f "${OFFLINE}.sha256" ]; then
        local checksum_dir checksum_file
        checksum_dir="$(cd "$(dirname "${OFFLINE}")" && pwd)"
        checksum_file="$(basename "${OFFLINE}").sha256"
        ( cd "${checksum_dir}" && sha256sum -c "${checksum_file}" ) ||
            die "the offline image checksum did not verify."
    else
        warn "no ${OFFLINE}.sha256 next to the tarball; checksum verification skipped."
    fi
    log "Loading the offline release image"
    ${SUDO} docker load --input "${OFFLINE}" >/dev/null
}

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

# --------------------------------------------------------------------------- #
# Docker Engine
# --------------------------------------------------------------------------- #

install_docker() {
    if ! command -v curl >/dev/null 2>&1; then
        if command -v apt-get >/dev/null 2>&1; then
            ${SUDO} apt-get update
            ${SUDO} apt-get install -y --no-install-recommends ca-certificates curl
        else
            die "curl is required."
        fi
    fi

    if command -v docker >/dev/null 2>&1 && ${SUDO} docker info >/dev/null 2>&1; then
        return
    fi

    log "Installing Docker Engine"

    if [ -r /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        case "${ID:-}" in
            debian|ubuntu|raspbian)
                ${SUDO} sh -c 'curl -fsSL https://get.docker.com | sh'
                ;;
            *)
                warn "unsupported distribution '${ID:-unknown}'; trying the generic installer"
                ${SUDO} sh -c 'curl -fsSL https://get.docker.com | sh'
                ;;
        esac
    else
        ${SUDO} sh -c 'curl -fsSL https://get.docker.com | sh'
    fi

    if command -v systemctl >/dev/null 2>&1; then
        ${SUDO} systemctl enable --now docker
    fi

    command -v docker >/dev/null 2>&1 || die "Docker did not install."
    ${SUDO} docker info >/dev/null 2>&1 || die "the Docker daemon is not reachable."
}

# --------------------------------------------------------------------------- #
# Swarm
# --------------------------------------------------------------------------- #

detect_advertise_addr() {
    if [ -n "${WAGGLE_SWARM_ADVERTISE_ADDR:-}" ]; then
        echo "${WAGGLE_SWARM_ADVERTISE_ADDR}"
        return
    fi
    # The address of the default route is the one other nodes can reach.
    ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") { print $(i + 1); exit }}'
}

ensure_swarm() {
    local state
    state="$(${SUDO} docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null || echo inactive)"
    if [ "${state}" = "active" ]; then
        [ "$(${SUDO} docker info --format '{{.Swarm.ControlAvailable}}')" = "true" ] ||
            die "This host is already a Swarm worker. Run the installer on its manager."
        log "Swarm already active on this host"
        return
    fi

    local addr
    addr="$(detect_advertise_addr)"
    [ -n "${addr}" ] || die "cannot determine the advertise address; set WAGGLE_SWARM_ADVERTISE_ADDR."

    log "Initialising a swarm on ${addr}"
    ${SUDO} docker swarm init --advertise-addr "${addr}" >/dev/null
}

ensure_network() {
    if ${SUDO} docker network inspect "${PROXY_NETWORK}" >/dev/null 2>&1; then
        return
    fi
    log "Creating the attachable overlay network '${PROXY_NETWORK}'"
    ${SUDO} docker network create --driver overlay --attachable "${PROXY_NETWORK}" >/dev/null
}

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

generate_secret() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -base64 48 | tr -d '\n'
    else
        od -An -N48 -tx1 /dev/urandom | tr -d ' \n'
    fi
}

write_env() {
    if [ -f "${ENV_FILE}" ]; then
        log "Keeping the existing ${ENV_FILE}"
        local saved_bind
        saved_bind="$(sed -n 's/^WAGGLE_HTTP_PORT=//p' "${ENV_FILE}" | tail -n 1)"
        HTTP_BIND="${WAGGLE_HTTP_PORT:-${saved_bind:-8000}}"
        if [ -n "${IMAGE}" ]; then
            set_env_value WAGGLE_IMAGE "${IMAGE}"
        fi
        return
    fi

    local secret allowed image_line
    secret="$(generate_secret)"
    allowed="${WAGGLE_ALLOWED_HOSTS:-localhost}"
    # The container healthcheck talks to 127.0.0.1, so it must always be allowed.
    if [ "${allowed}" != "*" ]; then
        case ",${allowed}," in
            *",127.0.0.1,"*) ;;
            *) allowed="${allowed},127.0.0.1,localhost" ;;
        esac
    fi
    image_line=""
    [ -z "${IMAGE}" ] || image_line="WAGGLE_IMAGE=${IMAGE}"
    log "Writing ${ENV_FILE}"
    (umask 077; cat >"${ENV_FILE}") <<EOF
# Generated by deploy/install.sh. Keep this file out of git.
WAGGLE_SECRET_KEY=${secret}
WAGGLE_SECRET_KEYS=${secret}
WAGGLE_DEBUG=0
WAGGLE_ALLOWED_HOSTS=${allowed}
WAGGLE_HTTP_PORT=${HTTP_BIND}
WAGGLE_PROXY_NETWORK=${PROXY_NETWORK}
${image_line}
# Set to 1 once TLS is in front. Cookies are marked Secure while this is 1.
WAGGLE_SECURE_COOKIES=${WAGGLE_SECURE_COOKIES:-0}
WAGGLE_SSL_REDIRECT=${WAGGLE_SSL_REDIRECT:-0}
# Optional Prometheus scrape token (leave empty to disable /metrics).
WAGGLE_METRICS_TOKEN=${WAGGLE_METRICS_TOKEN:-}
EOF
    chmod 600 "${ENV_FILE}" 2>/dev/null || true
}

wait_for_health() {
    log "Waiting for the control plane"
    local deadline=$((SECONDS + 180)) probe_host="${HTTP_BIND%:*}"
    case "${probe_host}" in
        "${HTTP_BIND}"|""|0.0.0.0) probe_host=127.0.0.1 ;;
    esac
    while [ "${SECONDS}" -lt "${deadline}" ]; do
        if curl -fsS --max-time 4 -H 'Host: localhost' -H 'X-Forwarded-Proto: https' \
            "http://${probe_host}:${HTTP_BIND##*:}/readyz" >/dev/null 2>&1; then
            return 0
        fi
        sleep 3
    done
    compose logs --tail 60 web || true
    die "the control plane did not become healthy within three minutes."
}

create_admin() {
    local user="${WAGGLE_ADMIN_USER:-admin}"
    local password="${WAGGLE_ADMIN_PASSWORD:-}"

    if [ -z "${password}" ]; then
        if [ -t 0 ]; then
            printf 'Admin username [%s]: ' "${user}"
            read -r answer
            user="${answer:-$user}"
            printf 'Admin password: '
            read -rs password
            printf '\n'
        else
            compose exec -T web python manage.py create_admin --generate --username "${user}"
            return
        fi
    fi

    [ -n "${password}" ] || die "an empty admin password is not allowed."

    compose exec -T -e "WAGGLE_ADMIN_USER=${user}" -e "WAGGLE_ADMIN_PASSWORD=${password}" \
        web python manage.py create_admin
}

# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

install_docker
ensure_swarm
require_pinned_image
verify_image
write_env
PROXY_NETWORK="$(sed -n 's/^WAGGLE_PROXY_NETWORK=//p' "${ENV_FILE}" | tail -n 1)"
PROXY_NETWORK="${WAGGLE_PROXY_NETWORK:-${PROXY_NETWORK:-paas_proxy}}"
ensure_network

if [ -n "${OFFLINE}" ]; then
    load_offline_image
    log "Starting Waggle from the offline image ${IMAGE}"
    compose up -d --no-build
elif [ -n "${IMAGE}" ]; then
    log "Pulling the pinned release image"
    compose pull web worker
    log "Starting Waggle from ${IMAGE}"
    compose up -d --no-build
else
    log "Building and starting Waggle from this checkout"
    compose up -d --build
fi

wait_for_health
create_admin

log "Reconciling servers with the swarm"
compose exec -T web python manage.py sync_cluster || warn "sync_cluster failed; run it again once the UI is reachable."

cat <<EOF

Waggle is running.

  UI      http://127.0.0.1:${HTTP_BIND##*:}/
  Logs    docker compose -f deploy/docker-compose.yml logs -f web
  Config  ${ENV_FILE}
  Backup  sudo bash ${DEPLOY_DIR}/backup.sh
  Upgrade sudo bash ${DEPLOY_DIR}/upgrade.sh --image ghcr.io/OWNER/waggle@sha256:…

Add worker nodes from the UI (Servers → Add server). Each node needs a
reachable address and an SSH key whose public half is in authorized_keys,
with passwordless sudo.

Before exposing this to the internet, put TLS in front of it, set
WAGGLE_SECURE_COOKIES=1 and WAGGLE_SSL_REDIRECT=1 in ${ENV_FILE}, then re-run
this script.
EOF
