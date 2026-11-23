#!/usr/bin/env bash
# Three isolated Linux Docker engines for testing Waggle on one Docker host.
set -euo pipefail

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

case "${1:-}" in
    -h|--help)
        printf '%s\n' "Usage: bash deploy/lab.sh" \
            "Optional: WAGGLE_LAB_NAME=waggle-lab, WAGGLE_LAB_PORT=8088, WAGGLE_LAB_DIND_IMAGE=docker:28.5.2-dind"
        exit 0
        ;;
    "") ;;
    *) die "Unknown option: $1" ;;
esac

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
native_path() { printf '%s\n' "$1"; }
if command -v cygpath >/dev/null 2>&1; then
    # Docker Desktop uses the native Windows CLI. Do not rewrite Linux paths
    # embedded in commands that will run *inside* a container.
    export MSYS_NO_PATHCONV=1
    export MSYS2_ARG_CONV_EXCL='*'
    native_path() { cygpath -aw "$1"; }
fi

project="${WAGGLE_LAB_NAME:-waggle-lab}"
port="${WAGGLE_LAB_PORT:-8088}"
dind="${WAGGLE_LAB_DIND_IMAGE:-docker:28.5.2-dind}"
terminal_enabled="${WAGGLE_LAB_TERMINAL:-1}"
[[ "$terminal_enabled" =~ ^[01]$ ]] || die "WAGGLE_LAB_TERMINAL must be 0 or 1."
[[ "$project" =~ ^[a-z][a-z0-9-]{0,31}$ ]] || die "WAGGLE_LAB_NAME must be a lowercase lab name."
[[ "$port" =~ ^[1-9][0-9]{0,4}$ ]] && (( port <= 65535 )) ||
    die "WAGGLE_LAB_PORT must be between 1 and 65535."
manager="${project}-manager"
worker1="${project}-worker-1"
worker2="${project}-worker-2"
network="${project}-net"
image="waggle:${project}-local"
label='io.waggle.lab'
state='/var/lib/waggle-lab'

command -v docker >/dev/null 2>&1 || die "Install Docker Engine or Docker Desktop first."
endpoint="${DOCKER_HOST:-$(docker context inspect "$(docker context show)" --format '{{.Endpoints.docker.Host}}')}"
case "$endpoint" in
    unix://*|npipe://*) ;;
    *) die "Select a local Docker socket/context before running this lab." ;;
esac
[ "$(docker info --format '{{.OSType}}' 2>/dev/null)" = linux ] ||
    die "Start Docker with the Linux container engine."
log "Local development only: the three nested Docker engines require privileged containers."

ensure_network() {
    if docker network inspect "$network" >/dev/null 2>&1; then
        local owner
        owner="$(docker network inspect --format "{{index .Labels \"$label\"}}" "$network")"
        [ "$owner" = "$project" ] || die "Network $network belongs to another project."
    else
        docker network create --driver bridge --label "$label=$project" "$network" >/dev/null
    fi
}

ensure_volume() {
    local volume="$1" owner
    if docker volume inspect "$volume" >/dev/null 2>&1; then
        owner="$(docker volume inspect --format "{{index .Labels \"$label\"}}" "$volume")"
        [ "$owner" = "$project" ] || die "Volume $volume belongs to another project."
    else
        docker volume create --label "$label=$project" "$volume" >/dev/null
    fi
}

ensure_node() {
    local name="$1" owner
    shift
    if docker container inspect "$name" >/dev/null 2>&1; then
        owner="$(docker container inspect --format "{{index .Config.Labels \"$label\"}}" "$name")"
        [ "$owner" = "$project" ] || die "Container $name belongs to another project."
        docker start "$name" >/dev/null
    else
        ensure_volume "${name}-docker"
        docker run -d --name "$name" --hostname "$name" --label "$label=$project" \
            --privileged --cgroupns private --restart unless-stopped --network "$network" \
            --volume "${name}-docker:/var/lib/docker" "$@" \
            "$dind" dockerd --host=unix:///var/run/docker.sock >/dev/null
    fi
}

wait_engine() {
    local name="$1" start=$SECONDS end=$((SECONDS + 100)) repaired=false
    until docker exec "$name" docker info >/dev/null 2>&1; do
        if [ "$repaired" = false ] && (( SECONDS - start >= 20 )); then
            if repair_stale_containerd_pid "$name"; then
                repaired=true
            fi
        fi
        if (( SECONDS >= end )); then
            docker logs --tail 35 "$name" >&2
            die "$name's Docker engine did not start."
        fi
        sleep 2
    done
}

repair_stale_containerd_pid() {
    # An abrupt Docker Desktop stop can leave this PID file in the outer
    # container's writable layer. After restart its PID can identify an
    # unrelated process. Never remove a live containerd PID or stored data.
    docker exec "$1" sh -eu -c '
        path=/var/run/docker/containerd/containerd.pid
        [ -f "$path" ] || exit 1
        pid="$(cat "$path")"
        case "$pid" in ""|*[!0-9]*) exit 1 ;; esac
        if grep -qx containerd "/proc/$pid/comm" 2>/dev/null; then
            exit 1
        fi
        [ "$(cat "$path")" = "$pid" ] || exit 1
        rm -f "$path"
        echo "Removed stale containerd PID file; waiting for Docker to recover."
    ' 2>/dev/null
}

node_address() {
    docker inspect --format "{{(index .NetworkSettings.Networks \"$network\").IPAddress}}" "$1"
}

engine() {
    local name="$1"
    shift
    docker exec "$name" docker "$@"
}

swarm_state() {
    engine "$1" info --format '{{.Swarm.LocalNodeState}}'
}

node_id() {
    engine "$1" info --format '{{.Swarm.NodeID}}'
}

ensure_network
ensure_volume "${project}-state"
log "Starting the 1 GiB manager and two Linux workers"
ensure_node "$manager" --memory=1g --memory-swap=1g \
    --publish "127.0.0.1:${port}:8000" --volume "${project}-state:${state}"
ensure_node "$worker1"
ensure_node "$worker2"

[ "$(docker inspect --format '{{.HostConfig.Memory}}' "$manager")" = 1073741824 ] ||
    die "$manager does not have the expected 1 GiB limit."
[ "$(docker port "$manager" 8000/tcp)" = "127.0.0.1:${port}" ] ||
    die "$manager is bound to a different port; use its original WAGGLE_LAB_PORT or a new lab name."

for node in "$manager" "$worker1" "$worker2"; do wait_engine "$node"; done

# Docker can start all three containers concurrently after a Desktop restart
# and give their bridge addresses out in a different order. Swarm persists
# the original advertised addresses. Restore creation order only when a
# mismatch is observed; never reinitialize the manager or rejoin the workers.
lab_addresses_match() {
    local node saved actual
    for node in "$manager" "$worker1" "$worker2"; do
        saved="$(engine "$node" info --format '{{.Swarm.NodeAddr}}')"
        actual="$(node_address "$node")"
        [ -z "$saved" ] || [ "$saved" = "$actual" ] || return 1
    done
}
if ! lab_addresses_match; then
    log "Restoring the lab's original Swarm addresses after Docker restarted"
    docker stop "$worker2" "$worker1" "$manager" >/dev/null
    docker start "$manager" >/dev/null
    docker start "$worker1" >/dev/null
    docker start "$worker2" >/dev/null
    for node in "$manager" "$worker1" "$worker2"; do wait_engine "$node"; done
    lab_addresses_match || die "Lab bridge addresses still differ from the saved Swarm addresses. State was preserved."
fi

manager_addr="$(node_address "$manager")"
case "$(swarm_state "$manager")" in
    inactive)
        engine "$manager" swarm init --advertise-addr "$manager_addr" --availability drain >/dev/null
        ;;
    active)
        [ "$(engine "$manager" info --format '{{.Swarm.ControlAvailable}}')" = true ] ||
            die "The lab manager is already joined as a worker."
        ;;
    *) die "The lab manager's Swarm is not ready." ;;
esac

worker_token=""
for worker in "$worker1" "$worker2"; do
    case "$(swarm_state "$worker")" in
        inactive)
            if [ -z "$worker_token" ]; then
                worker_token="$(engine "$manager" swarm join-token --quiet worker)"
            fi
            engine "$worker" swarm join --token "$worker_token" \
                --advertise-addr "$(node_address "$worker")" "${manager_addr}:2377" >/dev/null
            ;;
        active|pending)
            # A restarted worker can be pending while its existing manager
            # session reconnects. Its node ID must still belong to this lab.
            ;;
        *) die "$worker's Swarm is not ready." ;;
    esac
    id="$(node_id "$worker")"
    [ -n "$id" ] && [ "$(engine "$manager" node inspect --format '{{.Spec.Role}}' "$id")" = worker ] ||
        die "$worker did not join the lab's Swarm as a worker."
    deadline=$((SECONDS + 45))
    until [ "$(engine "$manager" node inspect --format '{{.Status.State}}' "$id")" = ready ]; do
        (( SECONDS < deadline )) || die "$worker did not become ready."
        sleep 2
    done
done
unset worker_token
engine "$manager" node update --availability drain "$(node_id "$manager")" >/dev/null
if ! engine "$manager" network inspect paas_proxy >/dev/null 2>&1; then
    engine "$manager" network create --driver overlay --attachable paas_proxy >/dev/null
fi

log "Building Waggle on the outer Docker host, then loading it into the capped manager"
docker build -f "$(native_path "$root/deploy/Dockerfile")" -t "$image" "$(native_path "$root")"
docker image save "$image" | docker exec -i "$manager" docker image load
docker cp "$(native_path "$root/deploy/docker-compose.yml")" "${manager}:${state}/docker-compose.yml"
docker exec "$manager" sh -eu -c '
    cd /var/lib/waggle-lab
    umask 077
    if [ ! -f .env ]; then
        printf "WAGGLE_SECRET_KEY=%s\n" "$(openssl rand -hex 32)" > .env
        printf "WAGGLE_ALLOWED_HOSTS=localhost,127.0.0.1\nWAGGLE_SECURE_COOKIES=0\nWAGGLE_SSL_REDIRECT=0\n" >> .env
    fi
    grep -q '^WAGGLE_SSL_REDIRECT=' .env || printf 'WAGGLE_SSL_REDIRECT=0\n' >> .env
    # A drained Swarm node cannot reliably attach a standalone container to
    # its overlay. The lab UI only needs the isolated local Compose bridge;
    # application tasks still attach to paas_proxy on the workers.
    printf "services:\n  web:\n    image: %s\n    networks: !override [default]\n    environment:\n      WAGGLE_WEB_TERMINAL: \"%s\"\n  worker:\n    image: %s\n" \
        "$1" "$2" "$1" > lab-compose.yml
' sh "$image" "$terminal_enabled"

compose() {
    docker exec "$manager" docker compose --project-directory "$state" \
        -f "$state/docker-compose.yml" -f "$state/lab-compose.yml" "$@"
}

log "Starting Waggle and registering both workers"
if ! compose up -d --no-build --wait --wait-timeout 150; then
    compose logs --tail 40 >&2 || true
    die "Waggle did not become healthy inside the manager."
fi
compose exec -T web python manage.py adopt_workers "$(node_id "$worker1")" "$(node_id "$worker2")"

if [ "$terminal_enabled" = 1 ]; then
    log "Configuring private, restricted SSH for the worker terminals"
    docker exec "$manager" sh -eu -c '
        cd /var/lib/waggle-lab
        umask 077
        if ! command -v ssh-keygen >/dev/null; then
            alpine="$(cut -d . -f1,2 /etc/alpine-release)"
            timeout -k 3 100 apk --no-progress --repositories-file /dev/null \
                --repository "https://mirrors.edge.kernel.org/alpine/v${alpine}/main" \
                add --no-cache openssh-keygen
        fi
        [ -f terminal-key ] || ssh-keygen -q -t ed25519 -N "" -f terminal-key
        ssh-keygen -y -f terminal-key > terminal-key.pub
    '
    ssh_state='/var/lib/docker/waggle-terminal'
    for worker in "$worker1" "$worker2"; do
        docker exec "$worker" mkdir -m 700 -p "$ssh_state"
        docker cp "$(native_path "$root/apps/deployments/terminal-guard.sh")" "${worker}:${ssh_state}/guard.sh"
        docker exec "$manager" cat "$state/terminal-key.pub" |
            docker exec -i "$worker" sh -eu -c '
                dir=/var/lib/docker/waggle-terminal
                umask 077
                chmod 700 "$dir/guard.sh"
                read -r key
                printf "restrict %s\n" "$key" > "$dir/authorized_keys"
            '
        docker exec -i "$worker" sh -eu <<'SETUP_TERMINAL_SSH'
dir=/var/lib/docker/waggle-terminal
umask 077
if ! command -v sshd >/dev/null; then
    alpine="$(cut -d . -f1,2 /etc/alpine-release)"
    timeout -k 3 100 apk --no-progress --repositories-file /dev/null \
        --repository "https://mirrors.edge.kernel.org/alpine/v${alpine}/main" \
        add --no-cache openssh-server openssh-keygen
fi
[ -f "$dir/host_key" ] || ssh-keygen -q -t ed25519 -N '' -f "$dir/host_key"
# Alpine's locked root account cannot use public-key authentication. On this
# dedicated lab container unlock it with a random, undisclosed password;
# this SSH server explicitly disables all password authentication.
if awk -F: '$1=="root" && $2 ~ /^[!*]/ { found=1 } END { exit !found }' /etc/shadow; then
    printf 'root:%s\n' "$(openssl rand -hex 32)" | chpasswd
fi
cat > "$dir/sshd_config" <<EOF
Port 2222
HostKey $dir/host_key
PidFile $dir/sshd.pid
AuthorizedKeysFile $dir/authorized_keys
ForceCommand $dir/guard.sh
PermitRootLogin prohibit-password
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitTTY no
DisableForwarding yes
AllowUsers root
LoginGraceTime 10
EOF
pid="$(cat "$dir/sshd.pid" 2>/dev/null || true)"
if [ -z "$pid" ] || ! grep -qx sshd "/proc/$pid/comm" 2>/dev/null; then
    /usr/sbin/sshd -f "$dir/sshd_config"
fi
SETUP_TERMINAL_SSH
        pin="$(docker exec "$worker" ssh-keygen -lf "$ssh_state/host_key.pub" | awk '{print $2}')"
        docker exec "$manager" cat "$state/terminal-key" |
            docker exec -i "$manager" docker compose --project-directory "$state" \
                -f "$state/docker-compose.yml" -f "$state/lab-compose.yml" exec -T web \
                python manage.py configure_terminal_access "$(node_id "$worker")" "$pin"
    done
fi

docker exec -i "$manager" sh -eu <<'CREATE_LAB_ADMIN'
    cd /var/lib/waggle-lab
    umask 077
    if [ ! -f admin-created ]; then
        [ -s admin-password ] || openssl rand -hex 24 > admin-password
        docker compose -f docker-compose.yml -f lab-compose.yml exec -T web sh -eu -c \
            'read -r WAGGLE_ADMIN_PASSWORD; export WAGGLE_ADMIN_PASSWORD; exec python manage.py create_admin' \
            < admin-password
        touch admin-created
    fi
CREATE_LAB_ADMIN
engine "$manager" node ls
printf '\nWaggle:   http://127.0.0.1:%s/login/\nUsername: admin\nPassword: docker exec %s cat %s/admin-password\n' \
    "$port" "$manager" "$state"
