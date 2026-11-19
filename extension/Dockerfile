# syntax=docker/dockerfile:1
# Docker Desktop extension image. Build from the repository root:
#   docker build -f extension/Dockerfile -t k8s-resilience-engine:0.1.0 .
# Public release (multi-architecture, semver tag; see docs/EXTENSION.md, "Publishing a beta to Docker Hub"):
#   docker buildx build --platform linux/amd64,linux/arm64 -f extension/Dockerfile --build-arg VERSION=0.1.0-beta.2 -t <hub-user>/k8s-resilience-engine:0.1.0-beta.2 --push .

# --- Backend runtime: the unmodified application plus the extension wrapper -----------------
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/app
WORKDIR /app
# The pinned Python base predates Debian's zlib security update. Upgrade only that runtime library.
RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends --only-upgrade zlib1g \
    && rm -rf /var/lib/apt/lists/*

# Runtime dependencies only: requirements.txt without what the extension never uses.
#  * test-only packages (pytest, iniconfig, pluggy, pygments);
#  * uvloop and httptools, optional uvicorn speed-ups (it falls back to asyncio and h11);
#  * watchfiles (only for `uvicorn --reload`) and websockets (the app serves no WebSocket route);
#  * the PostgreSQL driver: compose.yaml fixes DATABASE_URL to SQLite on the data volume.
# The pip download cache is a build cache mount, so repeat builds skip the downloads and the cache
# never becomes part of an image layer.
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    grep -viE '^(pytest|iniconfig|pluggy|pygments|uvloop|httptools|watchfiles|websockets)==' requirements.txt > /tmp/runtime.txt \
    && pip install --only-binary :all: -r /tmp/runtime.txt \
    && pip check \
    && rm /tmp/runtime.txt \
    && useradd --uid 10001 --user-group --create-home --shell /usr/sbin/nologin app

# Code stays root-owned so the unprivileged runtime user cannot modify it.
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY experiments ./experiments
# sample_data/ and deploy/ are deliberately not copied: the extension never serves fixture data, and
# declared deployment intent must come from an explicitly selected source, not a folder in the image.
COPY extension/__init__.py extension/asgi.py extension/runtime.py extension/cluster_connection.py extension/cluster_ingest.py extension/error_envelope.py extension/healthcheck.py extension/tracing_routes.py ./extension/

# --- Host helper: one static Go binary per operating system, for this image's own architecture ---
# Docker Desktop copies the binary for the user's operating system onto their computer. metadata.json
# has no architecture field, so each image carries the binaries for its own architecture (an arm64
# image runs on an arm64 host and holds arm64 binaries). Windows on arm64 is built only so the paths
# in metadata.json always exist; it is not supported or tested.
FROM --platform=$BUILDPLATFORM golang:1.26.11 AS helper
WORKDIR /src
COPY extension/helper/go.mod extension/helper/go.sum ./
# Module and compiler caches are build cache mounts: repeat builds neither download nor recompile the
# standard library for each target, and the caches stay out of the image.
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY extension/helper ./
ARG TARGETARCH
ENV CGO_ENABLED=0
# -p 2 keeps the compiler's memory use low enough for a small Docker Desktop VM.
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    for os in darwin linux windows; do \
        ext=""; [ "$os" = "windows" ] && ext=".exe"; \
        GOOS="$os" GOARCH="$TARGETARCH" go build -p 2 -trimpath -ldflags "-s -w" -o "/out/$os/resilience-helper$ext" . || exit 1; \
    done; \
    GOOS=windows GOARCH="$TARGETARCH" go build -p 2 -trimpath -ldflags "-s -w -H=windowsgui" -o /out/windows/resilience-helper-bg.exe .

# --- Render the existing dashboard pages to static files (build time only) ------------------
# This stage is defined after the helper stage on purpose: BuildKit builds independent stages at the
# same time, and the Go compile plus this Python import of the whole application together exhaust a
# small Docker Desktop VM (4 GB), which turned a one-second step into minutes of swapping. Binding the
# helper's output below makes this stage wait for the helper without copying anything into the layer.
FROM runtime AS render
COPY extension/build_ui.py ./extension/build_ui.py
RUN --mount=type=bind,from=helper,source=/out,target=/tmp/helper-done true
RUN python -m extension.build_ui /out

# --- Final extension image ------------------------------------------------------------------
# Build/install tooling and its vendored HTTP/archive libraries are not needed in the shipped backend.
FROM runtime
RUN python -m pip uninstall -y pip setuptools
LABEL org.opencontainers.image.title="K8s Resilience Engine" \
      org.opencontainers.image.description="Dependency-aware resilience assessment for Kubernetes deployments, inside Docker Desktop. Nothing is applied or stopped without your approval of exactly what will happen." \
      org.opencontainers.image.vendor="K8s Resilience Engine" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/AseshNemal/k8s-resilience-engine" \
      org.opencontainers.image.url="https://github.com/AseshNemal/k8s-resilience-engine" \
      org.opencontainers.image.documentation="https://github.com/AseshNemal/k8s-resilience-engine/blob/main/docs/EXTENSION.md" \
      com.docker.desktop.extension.api.version=">= 0.2.3" \
      com.docker.extension.detailed-description="Reads a local Kubernetes cluster and shows which workflows survive worker-node failures. A change is applied, or a worker node stopped on a test run, only after you approve exactly what will happen; everything is put back afterwards." \
      com.docker.extension.changelog="0.1.0-beta.2: image metadata (version, source, documentation, license) and a Docker Hub overview; no functional change from beta.1. First public beta: read-only assessment of the Docker Desktop Kubernetes cluster, ranked recommendations, and an optional approved Apply, Reset and node-failure test. Verified on macOS (Apple silicon); Windows and Linux are built but not yet tested on real machines." \
      com.docker.desktop.extension.icon="https://raw.githubusercontent.com/AseshNemal/k8s-resilience-engine/main/extension/icon.svg" \
      com.docker.extension.publisher-url="https://github.com/AseshNemal/k8s-resilience-engine" \
      com.docker.extension.additional-urls="[{\"title\":\"Source code and issues\",\"url\":\"https://github.com/AseshNemal/k8s-resilience-engine\"},{\"title\":\"Install and troubleshooting guide\",\"url\":\"https://raw.githubusercontent.com/AseshNemal/k8s-resilience-engine/main/docs/EXTENSION.md\"}]" \
      com.docker.extension.screenshots="[{\"alt\":\"Failure impact: which services go down if each worker stops\",\"url\":\"https://raw.githubusercontent.com/AseshNemal/k8s-resilience-engine/main/extension/screenshots/1-failure-impact.png\"},{\"alt\":\"Ranked recommendations\",\"url\":\"https://raw.githubusercontent.com/AseshNemal/k8s-resilience-engine/main/extension/screenshots/2-recommendations.png\"},{\"alt\":\"Fail a node: choose workers and watch the application\",\"url\":\"https://raw.githubusercontent.com/AseshNemal/k8s-resilience-engine/main/extension/screenshots/3-fail-a-node.png\"}]" \
      com.docker.extension.categories="kubernetes,utility-tools"

COPY extension/metadata.json extension/compose.yaml extension/icon.svg /
COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md /licenses/
COPY --from=render /out /ui
COPY extension/ui-src/shim.js extension/ui-src/chrome.js extension/ui-src/autoconnect.js extension/ui-src/namespaces.js extension/ui-src/trace-service.js extension/ui-src/tracing-panel.js extension/ui-src/bookinfo-tracing.js extension/ui-src/outstanding.js extension/ui-src/connect.js extension/ui-src/connect-auto.js extension/ui-src/docker.css /ui/
COPY --from=helper --chmod=0755 /out/darwin/resilience-helper /darwin/resilience-helper
COPY --from=helper --chmod=0755 /out/linux/resilience-helper /linux/resilience-helper
COPY --from=helper --chmod=0755 /out/windows/resilience-helper.exe /windows/resilience-helper.exe
# The same program without a console window: the Windows background task runs this one, so nothing flashes on screen.
COPY --from=helper --chmod=0755 /out/windows/resilience-helper-bg.exe /windows/resilience-helper-bg.exe
COPY --chmod=0755 extension/entrypoint.sh /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# Which commit and branch this image was built from. scripts/extension_manage.py passes it ("<commit>" or
# "<commit>-dirty" when the build included uncommitted changes); a plain `docker build` leaves "unknown".
# It is last in the file so a new commit id only rewrites this metadata, never an expensive layer.
ARG GIT_COMMIT=unknown
ARG GIT_BRANCH=unknown
ARG VERSION=unknown
LABEL org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${GIT_COMMIT}" \
      org.opencontainers.image.ref.name="${GIT_BRANCH}"
