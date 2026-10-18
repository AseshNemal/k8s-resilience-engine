#!/bin/sh
# Container entrypoint: prepare writable storage and the kubeconfig for the unprivileged
# app user, apply database migrations, then start the API server as UID 10001.
set -eu

# SQLite lives on the /data named volume. Named volumes are root-owned when first created.
install -d -o 10001 -g 10001 -m 0700 /data

# The host kubeconfig is bind-mounted read-only and stays private on the host
# (mode 0600, owned by the host user), so UID 10001 cannot read it directly.
# Copy it into a private in-memory tmpfs directory owned by the app user.
# /run/kube is a tmpfs mountpoint that starts out root-owned, so fix that first.
install -d -o 10001 -g 10001 -m 0700 /run/kube
install -o 10001 -g 10001 -m 0400 /run/secrets/kubeconfig /run/kube/config

exec setpriv --reuid=10001 --regid=10001 --init-groups -- \
    sh -c 'alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000'
