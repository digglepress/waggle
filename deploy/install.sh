#!/usr/bin/env bash
#
# Waggle installer — run this on the machine that will be the Swarm manager.
#
#   curl -fsSL https://raw.githubusercontent.com/<org>/waggle/main/deploy/install.sh | sudo bash
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
#   WAGGLE_HTTP_PORT=8000          host port for the UI
#   WAGGLE_ALLOWED_HOSTS=*         Django ALLOWED_HOSTS (comma separated)
#   WAGGLE_SECURE_COOKIES=0        set to 1 once TLS terminates in front
#   WAGGLE_ADMIN_USER=admin
#   WAGGLE_ADMIN_PASSWORD=...      if unset, you are prompted (or skipped)
#   WAGGLE_SWARM_ADVERTISE_ADDR=   address other nodes should use
#   WAGGLE_REPO_URL=...            where install.sh came from, for updates

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_DIR="${REPO_DIR}/deploy"
ENV_FILE="${DEPLOY_DIR}/.env"
HTTP_PORT="${WAGGLE_HTTP_PORT:-8000}"
PROXY_NETWORK="${WAGGLE_PROXY_NETWORK:-paas_proxy}"

log()  { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------------------- #
# Privileges
# --------------------------------------------------------------------------- #

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
elif command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
else
    die "run as root, or install sudo."
fi

if [ ! -f "${DEPLOY_DIR}/docker-compose.yml" ]; then
    die "cannot find deploy/docker-compose.yml next to install.sh (${DEPLOY_DIR})."
fi

compose() {
    if ${SUDO} docker compose version >/dev/null 2>&1; then
        ${SUDO} docker compose -f "${DEPLOY_DIR}/docker-compose.yml" "$@"
    elif command -v docker-compose >/dev/null 2>&1; then
        ${SUDO} docker-compose -f "${DEPLOY_DIR}/docker-compose.yml" "$@"
    else
        die "the Docker Compose plugin is missing. Install docker-compose-plugin."
    fi
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
        return
    fi

    local secret allowed
    secret="$(generate_secret)"
    allowed="${WAGGLE_ALLOWED_HOSTS:-*}"
    # The container healthcheck talks to 127.0.0.1, so it must always be allowed.
    if [ "${allowed}" != "*" ]; then
        case ",${allowed}," in
            *",127.0.0.1,"*) ;;
            *) allowed="${allowed},127.0.0.1,localhost" ;;
        esac
    fi
    log "Writing ${ENV_FILE}"
    cat >"${ENV_FILE}" <<EOF
# Generated by deploy/install.sh. Keep this file out of git.
WAGGLE_SECRET_KEY=${secret}
WAGGLE_DEBUG=0
WAGGLE_ALLOWED_HOSTS=${allowed}
WAGGLE_HTTP_PORT=${HTTP_PORT}
# M1 serves plain HTTP on :${HTTP_PORT}. Set to 1 once TLS is in front.
WAGGLE_SECURE_COOKIES=${WAGGLE_SECURE_COOKIES:-0}
EOF
    chmod 600 "${ENV_FILE}" 2>/dev/null || true
}

wait_for_health() {
    log "Waiting for the control plane"
    local deadline=$((SECONDS + 180))
    while [ "${SECONDS}" -lt "${deadline}" ]; do
        if curl -fsS "http://127.0.0.1:${HTTP_PORT}/healthz" >/dev/null 2>&1; then
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
            warn "no WAGGLE_ADMIN_PASSWORD set and no TTY; create an admin later with:"
            warn "  docker compose -f deploy/docker-compose.yml exec web python manage.py createsuperuser"
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
ensure_network
write_env

log "Building and starting Waggle"
compose up -d --build

wait_for_health
create_admin

log "Reconciling servers with the swarm"
compose exec -T web python manage.py sync_cluster || warn "sync_cluster failed; run it again once the UI is reachable."

cat <<EOF

Waggle is running.

  UI      http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 127.0.0.1):${HTTP_PORT}/
  Logs    docker compose -f deploy/docker-compose.yml logs -f web
  Config  ${ENV_FILE}

Add worker nodes from the UI (Servers → Add server). Each node needs a
reachable address and an SSH key whose public half is in authorized_keys,
with passwordless sudo.

Before exposing this to the internet, put TLS in front of it and set
WAGGLE_SECURE_COOKIES=1 in ${ENV_FILE}, then re-run this script.
EOF
