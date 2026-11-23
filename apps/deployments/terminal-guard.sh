#!/bin/sh
# The only command accepted by the lab's dedicated SSH key.
set -eu
deny() { printf '%s\n' "$1" >&2; exit 125; }
restricted=false
if [ "$#" -eq 0 ]; then
    restricted=true
    set -f
    # Split a fixed protocol with base64 input, never evaluate shell from SSH.
    set -- ${SSH_ORIGINAL_COMMAND:-}
    [ "$#" -eq 6 ] && [ "$1" = waggle-terminal ] || deny "Unsupported terminal request."
    shift
    decoded="$(printf '%s' "$5" | base64 -d)" || deny "Invalid terminal request."
    set -- "$1" "$2" "$3" "$4" "$decoded"
fi

[ "$#" -eq 5 ] || deny "Invalid terminal target."
node="$1" service="$2" task="$3" container="$4" command="$5"
case "$container" in ""|*[!0-9a-f]*) deny "Invalid container ID." ;; esac
[ "${#container}" -eq 64 ] || deny "Invalid container ID."
[ "$(docker info --format '{{.Swarm.NodeID}}')" = "$node" ] || deny "Docker node identity changed."
identity="$(docker inspect --type container --format \
    '{{.State.Running}}|{{index .Config.Labels "com.docker.swarm.service.id"}}|{{index .Config.Labels "com.docker.swarm.task.id"}}|{{index .Config.Labels "waggle.managed"}}' \
    "$container")" || deny "Container is no longer available."
IFS='|' read -r running actual_service actual_task managed <<EOF
$identity
EOF
[ "$running" = true ] && [ "$actual_service" = "$service" ] && [ "$actual_task" = "$task" ] ||
    deny "Container no longer belongs to this running task."
if [ "$restricted" = true ] && [ "$managed" != true ]; then
    deny "This container is not marked for terminal access. Redeploy it from Waggle."
fi

# The command is passed as an argument to the container shell, never the host shell.
exec docker exec "$container" timeout -k 2 20 /bin/sh -c "$command"
