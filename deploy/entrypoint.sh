#!/bin/sh
# Runs before both the web process and the Huey worker.
set -eu

if [ "${WAGGLE_SKIP_MIGRATE:-0}" != "1" ]; then
    echo "==> migrating"
    python manage.py migrate --noinput
    echo "==> collecting static files"
    python manage.py collectstatic --noinput
    echo "==> reconciling servers with the swarm"
    python manage.py sync_cluster || true
fi

exec "$@"
