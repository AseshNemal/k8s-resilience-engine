#!/bin/sh
# Extension backend entrypoint: prepare writable storage, apply migrations, then serve the API
# on a Unix socket only, as UID 10001. There is no TCP listener and no published port.
#
# Root keeps only CAP_CHOWN (plus the capabilities needed to switch user) here, so it cannot
# enter a directory that is already private to the app user. Each directory is therefore prepared
# once, while root still owns it, and left alone on later starts.
set -eu

SOCKET_DIR=/run/guest-services
SOCKET="$SOCKET_DIR/k8s-resilience-engine.sock"

prepare() {  # prepare DIR: make DIR private to the app user (chmod first, then chown)
    local dir="$1"
    if [ "$(stat -c %u "$dir")" != 10001 ]; then
        chmod 0700 "$dir"
        chown 10001:10001 "$dir"
    fi
}

# SQLite and the stored read-only kubeconfig (created by the app, mode 0700) live on /data.
prepare /data

# The socket directory is provided by Docker Desktop. uvicorn makes the socket file itself
# world-writable, so keeping the directory private to the app user is what restricts access.
mkdir -p "$SOCKET_DIR"
prepare "$SOCKET_DIR"

exec setpriv --reuid=10001 --regid=10001 --init-groups -- \
    sh -c 'umask 077 && rm -f "$1" && alembic upgrade head && exec uvicorn extension.asgi:application --uds "$1"' sh "$SOCKET"
